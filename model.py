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
            self.cache.move_to_end(key); return self.cache[key]

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
        self.current_matched_files = [] # ★★★ 修正: 属性を初期化

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
                                  meta_no_neg TEXT, width INTEGER, height INTEGER)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_mtime ON metadata_cache(mtime)')
                self.db_connection.commit()
        except sqlite3.Error as e:
            logging.error(f"データベース初期化失敗: {e}")
            if self.db_connection: self.db_connection.close()
    
    def get_metadata(self, file_path, exclude_negative=True):
        """★ 修正: 単一ファイル取得のメインロジック"""
        try:
            current_mtime = os.path.getmtime(file_path)
        except FileNotFoundError: return ""

        cache_key = f"{file_path}:{exclude_negative}:{current_mtime}"
        mem_cached = self.memory_cache.get(cache_key)
        if mem_cached is not None:
            return mem_cached

        with self.db_lock:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT * FROM metadata_cache WHERE file_path = ?", (file_path,))
            db_row = cursor.fetchone()

        if db_row and db_row['mtime'] == current_mtime:
            data_to_cache = db_row['meta_no_neg'] if exclude_negative else db_row['meta']
            self.memory_cache.set(cache_key, data_to_cache)
            return data_to_cache

        raw_meta = self._read_raw_metadata_from_disk(file_path)
        width, height = self._get_image_dimensions(file_path)
        meta_no_neg = self._filter_negative_prompt(raw_meta)
        
        db_data = (file_path, current_mtime, raw_meta, meta_no_neg, width, height)
        with self.db_lock:
            cursor = self.db_connection.cursor()
            cursor.execute("INSERT OR REPLACE INTO metadata_cache VALUES (?, ?, ?, ?, ?, ?)", db_data)
            self.db_connection.commit()

        data_to_cache = meta_no_neg if exclude_negative else raw_meta
        self.memory_cache.set(cache_key, data_to_cache)
        return data_to_cache
        
    def get_raw_metadata(self, file_path):
        with self.db_lock:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT meta FROM metadata_cache WHERE file_path = ?", (file_path,))
            row = cursor.fetchone()
        if row and row['meta']: return row['meta']
        return self._read_raw_metadata_from_disk(file_path)

    def _read_raw_metadata_from_disk(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext in ('.jpg', '.jpeg', '.tiff'): return self._extract_exif_text(file_path)
            elif ext in ('.png', '.webp'): return self._extract_png_text(file_path)
        except Exception as e: logging.warning(f"ディスク読込エラー: {file_path} -> {e}")
        return ""

    def _get_image_dimensions(self, file_path):
        try:
            with Image.open(file_path) as img: return img.size
        except Exception: return (0, 0)
    
    def _filter_negative_prompt(self, raw_meta):
        if not isinstance(raw_meta, str): return ""
        text_parts = []
        prompt_match = re.search(r'"prompt"\s*:\s*"([^"]*)"', raw_meta)
        if prompt_match: text_parts.append(prompt_match.group(1))
        a1111_match = re.search(r'^(.*?)\nNegative prompt: ', raw_meta, re.DOTALL)
        if a1111_match: text_parts.append(a1111_match.group(1).strip())
        base_match = re.search(r'"base_caption"\s*:\s*"([^"]*)"', raw_meta, re.IGNORECASE | re.DOTALL)
        if base_match: text_parts.append(base_match.group(1))
        char_matches = re.findall(r'"char_caption"\s*:\s*"([^"]*)"', raw_meta, re.IGNORECASE)
        text_parts.extend(char_matches)
        return " ".join(filter(None, text_parts)).strip()
    
    def get_resolution(self, file_path):
        with self.db_lock:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT width, height FROM metadata_cache WHERE file_path = ?", (file_path,))
            row = cursor.fetchone()
        if row and row['width'] is not None and row['height'] is not None:
            return row['width'] * row['height']
        w, h = self._get_image_dimensions(file_path)
        return w * h

    def _extract_exif_text(self, file_path):
        try:
            with open(file_path, 'rb') as f: return "\n".join(str(v) for v in exifread.process_file(f, details=False, stop_tag='JPEGThumbnail').values())
        except Exception: return ""
    
    def _extract_png_text(self, file_path):
        try:
            with Image.open(file_path) as img: return "\n".join(str(v) for v in img.info.values())
        except Exception: return ""
    
    def close(self):
        if self.db_connection: self.db_connection.close()
    
    def load_history(self):
        if not os.path.exists(self.history_file): return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f: return json.load(f)
        except (json.JSONDecodeError, IOError): return []

    def save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as f: json.dump(self.search_history, f, ensure_ascii=False, indent=2)
        except IOError as e: logging.error(f"履歴保存失敗: {e}")

    def add_history(self, cache_key):
        if not any(isinstance(i, (list, tuple)) and i[:3] == cache_key[:3] for i in self.search_history):
            self.search_history.append(list(cache_key)); self.save_history()

    def delete_history_item(self, item):
         if item in self.search_history: self.search_history.remove(item); self.save_history(); return True
         return False

    def load_favorite_settings(self):
        if not os.path.exists(self.favorites_file): return {}
        try:
            with open(self.favorites_file, "r", encoding="utf-8") as f: return json.load(f)
        except (json.JSONDecodeError, IOError): return {}

    def save_favorite_settings(self, settings):
        try:
            with open(self.favorites_file, "w", encoding="utf-8") as f: json.dump(settings, f, ensure_ascii=False, indent=2); return True
        except IOError as e: logging.error(f"お気に入り設定保存失敗: {e}"); return False

    def extract_json_block(self, text, start_key):
        if not isinstance(text, str): return None
        start_index = text.find(start_key);
        if start_index == -1: return None
        first_brace = text.find('{', start_index)
        if first_brace == -1: return None
        stack, in_string, escape, end_index = [], False, False, None
        for i in range(first_brace, len(text)):
            char = text[i]
            if char == '"' and not escape: in_string = not in_string
            if char == '\\' and not escape: escape = True
            else: escape = False
            if not in_string:
                if char == '{': stack.append('{')
                elif char == '}':
                    if stack:
                        stack.pop()
                        if not stack: end_index = i + 1; break
        if end_index is None: return None
        return re.sub(r',\s*([}\]])', r'\1', text[first_brace:end_index])
        
    def apply_sort(self, file_list, mode):
        reverse = "降順" in mode
        key_func = None
        if "ファイル名" in mode: key_func = lambda x: os.path.basename(x).lower()
        elif "更新日時" in mode: key_func = os.path.getmtime
        elif "解像度" in mode: key_func = self.get_resolution
        if key_func:
            existing_files = [f for f in file_list if os.path.exists(f)]
            return sorted(existing_files, key=key_func, reverse=reverse)
        return file_list

    def get_novelai_files_from_db(self, directory, limit):
        try:
            with self.db_lock:
                cursor = self.db_connection.cursor()
                query = '''SELECT file_path FROM metadata_cache WHERE file_path LIKE ? AND meta LIKE ? ORDER BY mtime DESC LIMIT ?'''
                path_pattern = f"{os.path.normpath(directory)}%"; meta_pattern = '%NovelAI%'
                cursor.execute(query, (path_pattern, meta_pattern, limit))
                return [row['file_path'] for row in cursor.fetchall()]
        except sqlite3.Error as e: logging.error(f"NovelAI画像のDB検索エラー: {e}"); return []