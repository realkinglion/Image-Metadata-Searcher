import os
import exifread
import json
import re
import logging
import collections
import sqlite3
import threading
from PIL import Image
from config import AppConfig

class ThreadSafeLRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = collections.OrderedDict()
        self._lock = threading.RLock()
    def get(self, key, default=None):
        with self._lock:
            if key not in self.cache: return default
            self.cache.move_to_end(key)
            return self.cache[key]
    def set(self, key, value):
        with self._lock:
            if self.capacity <= 0: return
            if key in self.cache: self.cache.move_to_end(key)
            elif len(self.cache) >= self.capacity: self.cache.popitem(last=False)
            self.cache[key] = value

class ImageSearchModel:
    def __init__(self, config: AppConfig):
        self.config = config
        self.history_file = "search_history.json"
        self.favorites_file = "favorites.json"
        self.memory_cache = ThreadSafeLRUCache(self.config.memory_cache_size)
        self.db_path = "metadata_cache.db"
        self.db_lock = threading.Lock()
        self.db_connection = None
        self._init_database()
        self.search_history = self.load_history()
        self.current_matched_files = []

    def _init_database(self):
        try:
            self.db_connection = sqlite3.connect(self.db_path, timeout=15, check_same_thread=False)
            self.db_connection.row_factory = sqlite3.Row
            with self.db_lock:
                cursor = self.db_connection.cursor()
                cursor.execute('PRAGMA journal_mode=WAL;')
                cursor.execute('PRAGMA synchronous=NORMAL;')
                cursor.execute('''CREATE TABLE IF NOT EXISTS metadata_cache (
                                  file_path TEXT PRIMARY KEY, mtime REAL NOT NULL, meta TEXT,
                                  meta_no_neg TEXT, width INTEGER, height INTEGER,
                                  thumbnail BLOB)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_mtime ON metadata_cache(mtime)')
                self.db_connection.commit()
        except sqlite3.Error as e:
            logging.error(f"データベース初期化失敗: {e}")
            if self.db_connection: self.db_connection.close()
    
    def get_metadata_and_thumbnail(self, file_path):
        try:
            current_mtime = os.path.getmtime(file_path)
        except FileNotFoundError:
            return "", None, file_path

        db_data = self._get_from_db(file_path)
        if db_data and db_data['mtime'] == current_mtime:
            return db_data['meta_no_neg'], db_data.get('thumbnail'), file_path

        raw_meta = self._read_raw_metadata_from_disk(file_path)
        width, height = self._get_image_dimensions(file_path)
        meta_no_neg = self._filter_negative_prompt(raw_meta)
        
        new_db_data = {'file_path': file_path, 'mtime': current_mtime, 'meta': raw_meta, 'meta_no_neg': meta_no_neg, 'width': width, 'height': height, 'thumbnail': None}
        self._save_to_db(new_db_data)
        
        return meta_no_neg, None, file_path

    def get_raw_metadata(self, file_path):
        db_data = self._get_from_db(file_path)
        if db_data and db_data.get('meta') is not None:
            return db_data['meta']
        return self._read_raw_metadata_from_disk(file_path)

    def _get_from_db(self, file_path):
        with self.db_lock:
            try:
                cursor = self.db_connection.cursor()
                cursor.execute("SELECT * FROM metadata_cache WHERE file_path = ?", (file_path,))
                row = cursor.fetchone()
                return dict(row) if row else None
            except sqlite3.Error as e:
                logging.error(f"DB読込エラー: {file_path}, {e}")
                return None

    def _save_to_db(self, data):
        with self.db_lock:
            try:
                cursor = self.db_connection.cursor()
                cursor.execute("INSERT OR REPLACE INTO metadata_cache VALUES (?,?,?,?,?,?,?)",
                               (data['file_path'], data['mtime'], data['meta'], data['meta_no_neg'],
                                data['width'], data['height'], data.get('thumbnail')))
                self.db_connection.commit()
            except sqlite3.Error as e:
                logging.error(f"DB書込エラー: {data['file_path']}, {e}")
    
    def cache_thumbnail(self, file_path, thumbnail_bytes):
        if not self.config.enable_thumbnail_caching: return
        with self.db_lock:
            try:
                cursor = self.db_connection.cursor()
                cursor.execute("UPDATE metadata_cache SET thumbnail = ? WHERE file_path = ?", (thumbnail_bytes, file_path))
                self.db_connection.commit()
            except sqlite3.Error as e:
                logging.error(f"サムネイルキャッシュ保存エラー: {file_path}, {e}")

    def _read_raw_metadata_from_disk(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext in ('.jpg', '.jpeg', '.tiff'):
                return self._extract_exif_text(file_path)
            elif ext in ('.png', '.webp'):
                return self._extract_png_text(file_path)
        except Exception as e:
            logging.warning(f"ディスク読込エラー: {file_path} -> {e}")
        return ""

    def _get_image_dimensions(self, file_path):
        try:
            with Image.open(file_path) as img:
                return img.size
        except Exception:
            return (0, 0)
    
    def _filter_negative_prompt(self, raw_meta):
        if not isinstance(raw_meta, str): return ""
        text_parts = []
        prompt_match = re.search(r'"prompt"\s*:\s*"([^"]*)"', raw_meta)
        if prompt_match: text_parts.append(prompt_match.group(1))
        a1111_match = re.search(r'^(.*?)\nNegative prompt: ', raw_meta, re.DOTALL)
        if a1111_match: text_parts.append(a1111_match.group(1).strip())
        base_match = re.search(r'"base_caption"\s*:\s*"([^"]*)"', raw_meta, re.IGNORECASE | re.DOTALL)
        if base_match: text_parts.append(base_match.group(1))
        char_captions = self._extract_char_captions_from_meta(raw_meta)
        text_parts.extend(char_captions)
        return " ".join(filter(None, text_parts)).strip()
    
    def get_resolution(self, file_path):
        db_data = self._get_from_db(file_path)
        if db_data and db_data.get('width') and db_data.get('height'):
            return db_data['width'] * db_data['height']
        w, h = self._get_image_dimensions(file_path)
        return w * h

    def _extract_exif_text(self, file_path):
        try:
            with open(file_path, 'rb') as f:
                return "\n".join(str(v) for v in exifread.process_file(f, details=False, stop_tag='JPEGThumbnail').values())
        except Exception:
            return ""
    
    def _extract_png_text(self, file_path):
        try:
            with Image.open(file_path) as img:
                return "\n".join(str(v) for v in img.info.values())
        except Exception:
            return ""
    
    def close(self):
        if self.db_connection:
            self.db_connection.close()
    
    def load_history(self):
        if not os.path.exists(self.history_file): return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.search_history, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logging.error(f"履歴保存失敗: {e}")

    def add_history(self, cache_key):
        current_history = self.load_history()
        key_to_add = list(cache_key)
        
        is_duplicate = any(
            isinstance(item, (list, tuple)) and item[:3] == key_to_add[:3]
            for item in current_history
        )

        if not is_duplicate:
            current_history.append(key_to_add)
            self.search_history = current_history
            self.save_history()

    def delete_history_item(self, item):
         current_history = self.load_history()
         if item in current_history:
             current_history.remove(item)
             self.search_history = current_history
             self.save_history()
             return True
         return False

    def load_favorite_settings(self):
        if not os.path.exists(self.favorites_file): return {}
        try:
            with open(self.favorites_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_favorite_settings(self, settings):
        try:
            with open(self.favorites_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
                return True
        except IOError as e:
            logging.error(f"お気に入り設定保存失敗: {e}")
            return False

    def extract_json_block(self, text, start_key):
        if not isinstance(text, str): return None
        start_index = text.find(start_key)
        if start_index == -1: return None
        first_brace = text.find('{', start_index)
        if first_brace == -1: return None
        stack, in_string, escape, end_index = [], False, False, None
        for i in range(first_brace, len(text)):
            char = text[i]
            if char == '"' and not escape:
                in_string = not in_string
            if char == '\\' and not escape:
                escape = True
            else:
                escape = False
            if not in_string:
                if char == '{':
                    stack.append('{')
                elif char == '}':
                    if stack:
                        stack.pop()
                        if not stack:
                            end_index = i + 1
                            break
        if end_index is None: return None
        return re.sub(r',\s*([}\]])', r'\1', text[first_brace:end_index])
        
    def apply_sort(self, file_list, mode):
        reverse = "降順" in mode
        key_func = None
        if "ファイル名" in mode:
            key_func = lambda x: os.path.basename(x).lower()
        elif "更新日時" in mode:
            key_func = os.path.getmtime
        elif "解像度" in mode:
            key_func = self.get_resolution
        if key_func:
            existing_files = [f for f in file_list if os.path.exists(f)]
            return sorted(existing_files, key=key_func, reverse=reverse)
        return file_list

    def get_novelai_files_from_db(self, directory, limit):
        """NovelAI画像をデータベースから検索（修正版）"""
        try:
            with self.db_lock:
                cursor = self.db_connection.cursor()
                
                query = '''
                    SELECT file_path, mtime FROM metadata_cache 
                    WHERE file_path LIKE ? 
                    AND (
                        LOWER(meta) LIKE '%"software": "novelai"%' OR
                        LOWER(meta) LIKE '%"application": "novelai"%' OR
                        LOWER(meta) LIKE '%software=novelai%' OR
                        LOWER(meta) LIKE '%created with novelai%'
                    )
                    ORDER BY mtime DESC 
                    LIMIT ?
                '''
                
                path_pattern = f"{os.path.normpath(directory)}%"
                cursor.execute(query, (path_pattern, limit))
                results = cursor.fetchall()
                
                logging.info(f"NovelAI検索: {directory} で {len(results)} 件見つかりました")
                return [row['file_path'] for row in results]
                
        except sqlite3.Error as e:
            logging.error(f"NovelAI画像のDB検索エラー: {e}")
            return []

    # ★★★ 変更点: 新しい高速版サジェスト取得メソッド ★★★
    def get_suggestions_from_metadata(self, dir_path, prefix, limit=50):
        if not dir_path or not prefix:
            return []
            
        try:
            with self.db_lock:
                cursor = self.db_connection.cursor()
                
                query = "SELECT meta_no_neg FROM metadata_cache WHERE file_path LIKE ? AND meta_no_neg LIKE ? ORDER BY mtime DESC LIMIT ?"
                
                path_pattern = f"{os.path.normpath(dir_path)}%"
                meta_pattern = f"%{prefix}%"
                
                cursor.execute(query, (path_pattern, meta_pattern, limit))
                
                word_set = set()
                # 正規表現をプリコンパイル
                regex = re.compile(r'\b' + re.escape(prefix) + r'[\w-]*', re.IGNORECASE)

                for row in cursor.fetchall():
                    meta_text = row['meta_no_neg']
                    if not meta_text: continue
                    
                    tokens = regex.findall(meta_text)
                    word_set.update(t.strip("()[]") for t in tokens)
                    
                    if len(word_set) >= 20:
                        break
                        
                return sorted(list(word_set))[:20]
                
        except sqlite3.Error as e:
            logging.error(f"メタデータからのキーワード候補取得エラー: {e}")
            return []

    def _extract_char_captions_from_meta(self, meta_text):
        """メタデータ文字列からキャラクタープロンプトのリストを抽出する"""
        json_str = self.extract_json_block(meta_text, '"v4_prompt"')
        if json_str:
            try:
                return [c.get("char_caption", "") for c in json.loads(json_str).get("caption", {}).get("char_captions", [])]
            except json.JSONDecodeError:
                pass
        return []

    def get_char_captions(self, file_path):
        """ファイルパスからキャラクタープロンプトのリストを取得する"""
        meta_text = self.get_raw_metadata(file_path)
        return self._extract_char_captions_from_meta(meta_text)

    def get_top_tags_from_files(self, file_paths, exclude_keywords=None, limit=20):
        """
        指定されたファイルパスのリストから、キャラクタープロンプト内の頻出タグを抽出する。
        """
        if not file_paths:
            return []

        if exclude_keywords is None:
            exclude_keywords = set()
        else:
            exclude_keywords = {kw.lower() for kw in re.split(r'[, ]+', exclude_keywords) if kw}

        tag_counter = collections.Counter()
        
        placeholders = ','.join('?' for _ in file_paths)
        query = f"SELECT meta FROM metadata_cache WHERE file_path IN ({placeholders})"

        try:
            with self.db_lock:
                cursor = self.db_connection.cursor()
                cursor.execute(query, file_paths)
                for row in cursor.fetchall():
                    meta_text = row['meta']
                    if not meta_text: continue
                    
                    char_captions = self._extract_char_captions_from_meta(meta_text)
                    for caption in char_captions:
                        tags = [tag.strip() for tag in caption.split(',') if tag.strip()]
                        valid_tags = [t for t in tags if t.lower() not in exclude_keywords and len(t) > 1]
                        tag_counter.update(valid_tags)
        except sqlite3.Error as e:
            logging.error(f"スマートタグ用のメタデータ取得エラー: {e}")
            return []

        return tag_counter.most_common(limit)