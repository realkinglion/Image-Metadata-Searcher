import os
import exifread
import shutil
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, simpledialog
from PIL import Image, ImageTk
import re
import json
import threading
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging
import sys
import subprocess

# Pillowのリサンプリング方法の互換性対応
try:
    from PIL import ImageResampling
    LANCZOS_RESAMPLING = ImageResampling.LANCZOS
except ImportError:
    # 古い環境用フォールバック
    LANCZOS_RESAMPLING = Image.LANCZOS

# ロギング設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class NewFileHandler(FileSystemEventHandler):
    """
    新しいファイルが追加された時に検知して検索にマッチするならリストに加える
    """
    def __init__(self, app):
        super().__init__()
        self.app = app

    def on_created(self, event):
        if event.is_directory:
            return

        file_path = event.src_path
        ext_lower = file_path.lower()
        if not ext_lower.endswith(('.jpg', '.jpeg', '.png', '.tiff')):
            return

        for i in range(5):
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'rb') as f:
                        _ = f.read(1024)
                    break
                except Exception:
                    time.sleep(0.1)
                    continue
            else:
                time.sleep(0.1)
        else:
            logging.warning(f"ファイルにアクセスできなかったためスキップ: {file_path}")
            return

        try:
            last_modified = os.path.getmtime(file_path)
        except Exception as e:
            logging.error(f"ファイル取得エラー: {file_path} -> {e}")
            return

        _ = self.app.get_normalized_metadata(
            file_path,
            exclude_negative=not self.app.include_negative_var.get()
        )

        meta_text = self.app.get_raw_metadata(file_path)

        keyword = self.app.keyword_var.get()
        match_type = self.app.match_type_var.get()

        if self.app.match_keyword(keyword, match_type, meta_text, file_path=file_path):
            with self.app.metadata_cache_lock:
                cache_entry = self.app.metadata_cache.get(file_path, {})
                cache_entry.update({
                    "mtime": last_modified,
                    "meta": meta_text,
                    "meta_no_neg": self.app.get_normalized_metadata(file_path, exclude_negative=True)
                })
                self.app.metadata_cache[file_path] = cache_entry
                
            self.app.queue.put({"type": "cache_update"})
            self.app.queue.put({
                "type": "new_file_matched",
                "file_path": file_path
            })


class ImageSearchApp:
    def __init__(self, root):
        self.root = root
        self.root.title("画像メタ情報検索くん v2.0 (機能統合版)")
        self.root.geometry("1600x900")

        style = ttk.Style()
        style.configure("TFrame", padding=5)
        style.configure("TLabel", padding=3)
        style.configure("TButton", padding=3)
        style.configure("TLabelframe.Label", padding=3)

        self.metadata_cache_lock = threading.Lock()
        self.current_matched_files_lock = threading.Lock()

        # --- メインフレーム設定 ---
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # --- 入力系全体フレーム ---
        top_controls_frame = ttk.Frame(main_frame)
        top_controls_frame.grid(row=0, column=0, sticky="ew")
        top_controls_frame.columnconfigure(1, weight=1)

        # --- 検索設定フレーム ---
        search_settings_frame = ttk.Labelframe(top_controls_frame, text="検索設定")
        search_settings_frame.grid(row=0, column=0, padx=5, pady=5, sticky="ns")

        # 検索対象フォルダ
        dir_label = ttk.Label(search_settings_frame, text="検索対象フォルダ:")
        dir_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.dir_path_var = tk.StringVar()
        self.dir_path_entry = ttk.Entry(search_settings_frame, textvariable=self.dir_path_var, width=50)
        self.dir_path_entry.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
        self.add_context_menu(self.dir_path_entry)
        browse_button = ttk.Button(search_settings_frame, text="フォルダ選択", command=self.browse_directory)
        browse_button.grid(row=1, column=2, padx=5, pady=5, sticky=tk.W)

        # 検索キーワード
        keyword_label = ttk.Label(search_settings_frame, text="検索キーワード:")
        keyword_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
        self.keyword_var = tk.StringVar()
        keyword_entry = ttk.Entry(search_settings_frame, textvariable=self.keyword_var, width=50)
        keyword_entry.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
        keyword_entry.bind("<Return>", self.search_images_event)
        self.add_context_menu(keyword_entry)
        load_prompt_button = ttk.Button(search_settings_frame, text="画像から読込", command=self.load_image_prompt)
        load_prompt_button.grid(row=3, column=2, padx=5, pady=5, sticky=tk.W)
        
        # 検索オプション
        options_frame = ttk.Frame(search_settings_frame)
        options_frame.grid(row=4, column=0, columnspan=3, sticky="w", padx=5, pady=5)
        
        self.match_type_var = tk.StringVar(value="partial")
        partial_radio = ttk.Radiobutton(options_frame, text="一部一致", variable=self.match_type_var, value="partial")
        partial_radio.grid(row=0, column=0, padx=(0,5))
        exact_radio = ttk.Radiobutton(options_frame, text="完全一致", variable=self.match_type_var, value="exact")
        exact_radio.grid(row=0, column=1, padx=5)
        
        self.and_search_var = tk.BooleanVar(value=True)
        and_check = ttk.Checkbutton(options_frame, text="AND検索", variable=self.and_search_var)
        and_check.grid(row=0, column=2, padx=5)

        self.include_negative_var = tk.BooleanVar(value=False)
        include_negative_cb = ttk.Checkbutton(options_frame, text="ネガティブ含む", variable=self.include_negative_var)
        include_negative_cb.grid(row=0, column=3, padx=5)

        self.recursive_search_var = tk.BooleanVar(value=True)
        recursive_cb = ttk.Checkbutton(options_frame, text="サブフォルダも検索", variable=self.recursive_search_var)
        recursive_cb.grid(row=0, column=4, padx=5)
        
        # 検索ボタンとプログレスバー
        search_button_frame = ttk.Frame(search_settings_frame)
        search_button_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=10)

        search_button = tk.Button(search_button_frame,
                                  text="検索スタート",
                                  command=self.search_images,
                                  bg="#007bff",
                                  fg="white",
                                  activebackground="#0056b3",
                                  activeforeground="white",
                                  font=("", 10, "bold"),
                                  relief="raised",
                                  bd=2,
                                  padx=10, pady=3)
        
        search_button.pack(side=tk.LEFT, padx=5)
        self.progress = ttk.Progressbar(search_button_frame, orient="horizontal", length=300, mode="determinate")
        self.progress.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # --- 機能フレーム ---
        action_frame = ttk.Labelframe(top_controls_frame, text="機能")
        action_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        action_frame.columnconfigure(1, weight=1)

        # 1行目: 履歴の選択と削除
        history_label = ttk.Label(action_frame, text="検索履歴:")
        history_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        
        self.history_var = tk.StringVar()
        self.history_combo = ttk.Combobox(action_frame, textvariable=self.history_var, state="readonly")
        self.history_combo.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.history_combo.bind("<<ComboboxSelected>>", self.on_history_selected)

        del_history_button = ttk.Button(action_frame, text="選択履歴削除", command=self.delete_selected_history)
        del_history_button.grid(row=0, column=2, padx=5, pady=5, sticky="w")

        # 2行目: 履歴のソートとお気に入り
        history_sort_label = ttk.Label(action_frame, text="履歴ソート:")
        history_sort_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        
        self.history_sort_var = tk.StringVar(value="追加順")
        history_sort_combobox = ttk.Combobox(action_frame, textvariable=self.history_sort_var, state="readonly", width=10, values=("追加順", "ディレクトリ順", "キーワード順"))
        history_sort_combobox.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        history_sort_combobox.bind("<<ComboboxSelected>>", lambda e: self.update_history_combobox())

        fav_save_button = ttk.Button(action_frame, text="お気に入り設定保存", command=self.save_favorite_settings)
        fav_save_button.grid(row=1, column=2, padx=5, pady=5, sticky="w")

        # 3行目: ファイル操作
        file_op_frame = ttk.Labelframe(action_frame, text="選択したファイルを...")
        file_op_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=5, pady=5)
        file_op_frame.columnconfigure(1, weight=1)
        
        dest_label = ttk.Label(file_op_frame, text="保存先フォルダ:")
        dest_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.dest_path_var = tk.StringVar()
        dest_entry = ttk.Entry(file_op_frame, textvariable=self.dest_path_var)
        dest_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.add_context_menu(dest_entry)
        browse_dest_button = ttk.Button(file_op_frame, text="保存先選択", command=self.browse_dest_directory)
        browse_dest_button.grid(row=0, column=2, padx=5, pady=5)
        copy_button = ttk.Button(file_op_frame, text="コピー", command=self.copy_selected_files)
        copy_button.grid(row=0, column=3, padx=5, pady=5)
        move_button = ttk.Button(file_op_frame, text="移動", command=self.move_selected_files)
        move_button.grid(row=0, column=4, padx=5, pady=5)
        
        # 4行目: NovelAI画像表示
        novel_ai_frame = ttk.Frame(action_frame)
        novel_ai_frame.grid(row=3, column=0, columnspan=3, sticky="w", padx=5, pady=5)
        novel_ai_label = ttk.Label(novel_ai_frame, text="最新NAI画像表示件数:")
        novel_ai_label.pack(side=tk.LEFT)
        self.novel_ai_count_var = tk.IntVar(value=10)
        novel_ai_count_entry = ttk.Entry(novel_ai_frame, textvariable=self.novel_ai_count_var, width=5)
        novel_ai_count_entry.pack(side=tk.LEFT, padx=5)
        self.add_context_menu(novel_ai_count_entry)
        novel_ai_button = ttk.Button(novel_ai_frame, text="最新NovelAI画像表示", command=self.show_novel_ai_images)
        novel_ai_button.pack(side=tk.LEFT, padx=5)
        
        # 表示・ソート設定をLabelframeにまとめる
        display_settings_frame = ttk.Labelframe(top_controls_frame, text="表示・ソート設定")
        display_settings_frame.grid(row=0, column=2, padx=5, pady=5, sticky="ns")

        # 検索結果ソート
        sort_label = ttk.Label(display_settings_frame, text="検索結果ソート:")
        sort_label.grid(row=0, column=0, sticky=tk.W, padx=5)
        self.sort_var = tk.StringVar(value="更新日時降順")
        sort_combobox = ttk.Combobox(display_settings_frame, textvariable=self.sort_var, state="readonly", width=15,
                                     values=("ファイル名昇順", "ファイル名降順", "更新日時昇順", "更新日時降順", "解像度(昇順)", "解像度(降順)"))
        sort_combobox.grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        sort_combobox.bind("<<ComboboxSelected>>", self.on_sort_changed)

        # 最大表示件数
        max_display_label = ttk.Label(display_settings_frame, text="最大表示件数:")
        max_display_label.grid(row=2, column=0, sticky=tk.W, padx=5, pady=(10,0))
        self.max_display_var = tk.IntVar(value=20)
        max_display_entry = ttk.Entry(display_settings_frame, textvariable=self.max_display_var, width=5)
        max_display_entry.grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        self.add_context_menu(max_display_entry)
        
        # 全選択/解除ボタンを常設
        select_all_btn = ttk.Button(display_settings_frame, text="すべて選択", command=self.select_all_files)
        select_all_btn.grid(row=4, column=0, sticky="ew", padx=5, pady=(10,2))
        deselect_all_btn = ttk.Button(display_settings_frame, text="すべて解除", command=self.deselect_all_files)
        deselect_all_btn.grid(row=5, column=0, sticky="ew", padx=5, pady=2)

        # ページング
        paging_frame = ttk.Frame(display_settings_frame)
        paging_frame.grid(row=6, column=0, sticky="ew", padx=5, pady=10)
        prev_page_btn = ttk.Button(paging_frame, text="前", command=self.prev_page)
        prev_page_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)
        next_page_btn = ttk.Button(paging_frame, text="次", command=self.next_page)
        next_page_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.page_info_label = ttk.Label(display_settings_frame, text="ページ 1/1")
        self.page_info_label.grid(row=7, column=0, pady=2)
        
        # ページジャンプ機能
        page_jump_frame = ttk.Frame(display_settings_frame)
        page_jump_frame.grid(row=8, column=0, sticky="ew", padx=5, pady=2)
        self.page_jump_var = tk.StringVar()
        page_jump_entry = ttk.Entry(page_jump_frame, textvariable=self.page_jump_var, width=5)
        page_jump_entry.pack(side=tk.LEFT, padx=(0,5))
        page_jump_btn = ttk.Button(page_jump_frame, text="ページ移動", command=self.jump_to_page)
        page_jump_btn.pack(side=tk.LEFT)

        # --- 結果表示フレーム ---
        self.results_frame = ttk.Frame(main_frame, borderwidth=2, relief="sunken")
        self.results_frame.grid(row=1, column=0, padx=5, pady=5, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.results_frame.rowconfigure(0, weight=1)
        self.results_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self.results_frame)
        self.canvas.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.scrollbar = ttk.Scrollbar(self.results_frame, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.results_inner_frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.results_inner_frame, anchor="nw")
        
        self.results_inner_frame.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

        self._resize_after_id = None
        self.results_frame.bind("<Configure>", self.on_resize_frame)

        # --- 初期化 ---
        self.thumb_size = (200, 200)
        self.thumbnails = []
        self.search_cache = {}
        self.history_file = "search_history.json"
        self.search_history = self.load_history()
        self.sorted_search_history = []
        self.current_matched_files = []
        self.selected_files = {}
        self.metadata_cache_file = "metadata_cache.json"
        self.metadata_cache = self.load_metadata_cache()
        self.favorites_file = "favorites.json"
        self.current_page = 0
        self.total_pages = 1
        self.queue = queue.Queue()
        self.observer = None
        self.cache_dirty = False
        self.cache_save_after_id = None

        self.update_history_combobox()
        self.root.after(100, self.process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.load_favorite_settings()

    # ▼▼▼【ここから機能移植】▼▼▼
    # 移植元の v1.10 にあった、プロンプト関連の優れた機能を再実装します。

    def extract_json_block(self, text, start_key):
        start_index = text.find(start_key)
        if start_index == -1:
            return None

        first_brace = text.find('{', start_index)
        if first_brace == -1:
            return None

        stack = []
        in_string = False
        escape = False
        end_index = None

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
        if end_index is None:
            return None
        json_str = text[first_brace:end_index]
        # 末尾の余分なカンマを削除する
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        return json_str

    def copy_base_caption(self, file_path):
        meta_text = self.get_raw_metadata(file_path)
        json_str = self.extract_json_block(meta_text, '"v4_prompt"')
        if not json_str:
            messagebox.showerror("エラー", "ベースプロンプトの抽出に失敗しました。")
            return
        try:
            data = json.loads(json_str)
            caption_block = data.get("caption", {})
            base_caption = caption_block.get("base_caption", "")
            if base_caption:
                self.copy_to_clipboard(base_caption, "ベースプロンプト")
            else:
                messagebox.showerror("エラー", "ベースプロンプトが見つかりませんでした。")
        except json.JSONDecodeError as e:
            snippet = json_str[:200] + "..." if len(json_str) > 200 else json_str
            messagebox.showerror("エラー", f"JSONパースエラー: {e}\n抽出文字列:\n{snippet}")

    def copy_char_caption(self, file_path, index):
        meta_text = self.get_raw_metadata(file_path)
        json_str = self.extract_json_block(meta_text, '"v4_prompt"')
        if not json_str:
            messagebox.showerror("エラー", "キャラクタープロンプトの抽出に失敗しました。")
            return
        try:
            data = json.loads(json_str)
            caption_block = data.get("caption", {})
            char_captions = caption_block.get("char_captions", [])
            if isinstance(char_captions, list) and len(char_captions) > index:
                selected_caption = char_captions[index].get("char_caption", "")
                display_text = (selected_caption[:15] + "…") if len(selected_caption) > 15 else selected_caption
                self.copy_to_clipboard(selected_caption, f"キャラクタープロンプト ({display_text})")
            else:
                messagebox.showerror("エラー", "指定のキャラクタープロンプトが見つかりませんでした。")
        except json.JSONDecodeError as e:
            snippet = json_str[:200] + "..." if len(json_str) > 200 else json_str
            messagebox.showerror("エラー", f"JSONパースエラー: {e}\n抽出文字列:\n{snippet}")

    def copy_base_negative(self, file_path):
        meta_text = self.get_raw_metadata(file_path)
        json_str = self.extract_json_block(meta_text, '"v4_negative_prompt"')
        if not json_str:
            messagebox.showerror("エラー", "ベースネガティブの抽出に失敗しました。")
            return
        try:
            data = json.loads(json_str)
            caption_block = data.get("caption", {})
            base_negative = caption_block.get("base_caption", "")
            if base_negative:
                self.copy_to_clipboard(base_negative, "ベースネガティブ")
            else:
                messagebox.showerror("エラー", "ベースネガティブが見つかりませんでした。")
        except json.JSONDecodeError as e:
            snippet = json_str[:200] + "..." if len(json_str) > 200 else json_str
            messagebox.showerror("エラー", f"JSONパースエラー: {e}\n抽出文字列:\n{snippet}")

    def copy_char_negative(self, file_path, index):
        meta_text = self.get_raw_metadata(file_path)
        json_str = self.extract_json_block(meta_text, '"v4_negative_prompt"')
        if not json_str:
            messagebox.showerror("エラー", "キャラクターネガティブの抽出に失敗しました。")
            return
        try:
            data = json.loads(json_str)
            caption_block = data.get("caption", {})
            char_negatives = caption_block.get("char_captions", [])
            if isinstance(char_negatives, list) and len(char_negatives) > index:
                selected_negative = char_negatives[index].get("char_caption", "")
                display_text = (selected_negative[:15] + "…") if len(selected_negative) > 15 else selected_negative
                self.copy_to_clipboard(selected_negative, f"キャラクターネガティブ ({display_text})")
            else:
                messagebox.showerror("エラー", "指定のキャラクターネガティブが見つかりませんでした。")
        except json.JSONDecodeError as e:
            snippet = json_str[:200] + "..." if len(json_str) > 200 else json_str
            messagebox.showerror("エラー", f"JSONパースエラー: {e}\n抽出文字列:\n{snippet}")
            
    # ▲▲▲【ここまで機能移植】▲▲▲


    # =====================================
    # UIイベントハンドラ・メソッド群
    # =====================================

    def add_context_menu(self, widget):
        context_menu = tk.Menu(widget, tearoff=0)
        context_menu.add_command(label="切り取り", command=lambda: widget.event_generate("<<Cut>>"))
        context_menu.add_command(label="コピー", command=lambda: widget.event_generate("<<Copy>>"))
        context_menu.add_command(label="ペースト", command=lambda: widget.event_generate("<<Paste>>"))
        def show_menu(event):
            context_menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-3>", show_menu)

    def on_closing(self):
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()
        self.save_metadata_cache()
        self.root.destroy()

    def process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg["type"] == "progress":
                    self.progress["value"] = msg["value"]
                elif msg["type"] == "done":
                    matched_files = msg["matched_files"]
                    cache_key = msg["cache_key"]
                    self.search_cache[cache_key] = matched_files
                    
                    history_exists = False
                    for item in self.search_history:
                        if isinstance(item, (list, tuple)) and item[:3] == cache_key[:3]:
                             history_exists = True
                             break
                    if not history_exists:
                        self.search_history.append(list(cache_key))
                        self.save_history()
                        self.update_history_combobox()

                    with self.current_matched_files_lock:
                        self.current_matched_files = matched_files
                    self.layout_results()
                    self.progress["value"] = 0
                elif msg["type"] == "cache_update":
                    self.cache_dirty = True
                    if self.cache_save_after_id is None:
                        self.cache_save_after_id = self.root.after(5000, self._debounced_cache_save)
                elif msg["type"] == "new_file_matched":
                    file_path = msg["file_path"]
                    with self.current_matched_files_lock:
                        if file_path not in self.current_matched_files:
                            self.current_matched_files.append(file_path)
                    self.layout_results(refresh=False)
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    def _bind_mousewheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
    def on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_resize_frame(self, event):
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(300, self.layout_results)

    def on_sort_changed(self, event):
        self.layout_results()

    def on_history_selected(self, event):
        idx = self.history_combo.current()
        if idx < 0:
            return
        item = self.sorted_search_history[idx]
        if isinstance(item, (tuple, list)) and len(item) >= 3:
            directory, match_type, keyword = item[:3]
            self.dir_path_var.set(directory)
            self.match_type_var.set(match_type)
            self.keyword_var.set(keyword)
            if len(item) > 3: self.include_negative_var.set(item[3])
            if len(item) > 4: self.and_search_var.set(item[4])
            if len(item) > 5: self.recursive_search_var.set(item[5])
            self.search_images()

    # =====================================
    # 設定・キャッシュ・履歴管理
    # =====================================

    def save_favorite_settings(self):
        favorite = {
            "dir_path": self.dir_path_var.get(),
            "keyword": self.keyword_var.get(),
            "match_type": self.match_type_var.get(),
            "sort": self.sort_var.get(),
            "include_negative": self.include_negative_var.get(),
            "and_search": self.and_search_var.get(),
            "recursive_search": self.recursive_search_var.get(),
            "max_display": self.max_display_var.get(),
            "novel_ai_count": self.novel_ai_count_var.get(),
        }
        try:
            with open(self.favorites_file, "w", encoding="utf-8") as f:
                json.dump(favorite, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("情報", "お気に入り設定を保存しました。")
        except Exception as e:
            messagebox.showerror("エラー", f"お気に入り保存失敗: {e}")

    def load_favorite_settings(self):
        if os.path.exists(self.favorites_file):
            try:
                with open(self.favorites_file, "r", encoding="utf-8") as f:
                    favorite = json.load(f)
                self.dir_path_var.set(favorite.get("dir_path", ""))
                self.keyword_var.set(favorite.get("keyword", ""))
                self.match_type_var.set(favorite.get("match_type", "partial"))
                self.sort_var.set(favorite.get("sort", "更新日時降順"))
                self.include_negative_var.set(favorite.get("include_negative", False))
                self.and_search_var.set(favorite.get("and_search", True))
                self.recursive_search_var.set(favorite.get("recursive_search", True))
                self.max_display_var.set(favorite.get("max_display", 20))
                self.novel_ai_count_var.set(favorite.get("novel_ai_count", 10))
                if self.dir_path_var.get():
                    self.start_directory_watch(self.dir_path_var.get())
            except Exception as e:
                logging.error(f"お気に入り読み込みエラー: {e}")

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.search_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"履歴保存エラー: {e}")

    def delete_selected_history(self):
        selected_idx = self.history_combo.current()
        if selected_idx < 0:
            messagebox.showinfo("情報", "削除する履歴が選択されていません。")
            return
            
        item_to_delete = self.sorted_search_history[selected_idx]
        
        if item_to_delete in self.search_history:
            self.search_history.remove(item_to_delete)
            self.save_history()
            self.update_history_combobox()
            self.history_var.set("")
            messagebox.showinfo("情報", "選択した履歴を削除しました。")
        else:
            messagebox.showerror("エラー", "履歴の削除に失敗しました。内部リストに不整合があります。")

    def update_history_combobox(self):
        sort_option = self.history_sort_var.get()
        self.search_history = [list(item) if isinstance(item, tuple) else item for item in self.search_history]
        sorted_history = self.search_history.copy()
        
        def get_sort_key(item, index, default=""):
            return str(item[index]).lower() if isinstance(item, (tuple, list)) and len(item) > index else default

        try:
            if sort_option == "ディレクトリ順":
                sorted_history.sort(key=lambda x: get_sort_key(x, 0))
            elif sort_option == "キーワード順":
                sorted_history.sort(key=lambda x: get_sort_key(x, 2))
        except IndexError:
             logging.warning("履歴のソート中にエラーが発生しました。旧フォーマットの可能性があります。")

        display_list = []
        for item in sorted_history:
            if isinstance(item, (tuple, list)) and len(item) >= 3:
                d, m, k = item[:3]
                display_list.append(f"{d} | {m} | {k}")
            else:
                display_list.append(str(item)) 
        
        self.history_combo["values"] = display_list
        self.sorted_search_history = sorted_history

    def load_metadata_cache(self):
        if os.path.exists(self.metadata_cache_file):
            try:
                with open(self.metadata_cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"メタキャッシュ読み込みエラー: {e}")
                return {}
        return {}

    def save_metadata_cache(self):
        with self.metadata_cache_lock:
            if not self.cache_dirty:
                return
            try:
                temp_file = self.metadata_cache_file + ".tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self.metadata_cache, f, ensure_ascii=False) 
                shutil.move(temp_file, self.metadata_cache_file)
                self.cache_dirty = False
                logging.info("メタデータキャッシュを保存しました。")
            except Exception as e:
                logging.error(f"メタキャッシュ保存エラー: {e}")
    
    def _debounced_cache_save(self):
        self.save_metadata_cache()
        self.cache_save_after_id = None

    # =====================================
    # 検索・ファイル処理
    # =====================================
    
    def search_images_event(self, event):
        self.search_images()

    def search_images(self):
        directory = self.dir_path_var.get()
        keyword = self.keyword_var.get()
        if not directory or not os.path.isdir(directory):
            messagebox.showerror("エラー", "検索対象フォルダを指定してください。")
            return
        if not keyword:
            messagebox.showerror("エラー", "検索キーワードを入力してください。")
            return

        self.current_page = 0
        self.progress["value"] = 0
        self.start_directory_watch(directory)

        cache_key = (
            directory, 
            self.match_type_var.get(), 
            keyword,
            self.include_negative_var.get(),
            self.and_search_var.get(),
            self.recursive_search_var.get()
        )

        th = threading.Thread(target=self._search_in_background, args=(directory, keyword, cache_key))
        th.daemon = True
        th.start()

    def _search_in_background(self, directory, keyword, cache_key):
        all_files = []
        recursive = self.recursive_search_var.get()
        if recursive:
            for root_dir, _, files in os.walk(directory):
                for file in files:
                    all_files.append(os.path.join(root_dir, file))
        else:
            try:
                for item in os.listdir(directory):
                    full_path = os.path.join(directory, item)
                    if os.path.isfile(full_path):
                        all_files.append(full_path)
            except OSError as e:
                 logging.error(f"ディレクトリへのアクセスエラー: {directory} -> {e}")

        total_files = len(all_files)
        if total_files == 0:
            self.queue.put({"type": "done", "matched_files": [], "cache_key": cache_key})
            return
        
        matched_files = []
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
            args_list = [(f, keyword, self.match_type_var.get()) for f in all_files]
            futures = [executor.submit(self.process_file, arg) for arg in args_list]
            
            for i, f in enumerate(futures):
                try:
                    is_matched = f.result()
                    if is_matched:
                        matched_files.append(all_files[i])
                except Exception as e:
                    logging.error(f"Future結果取得時に例外: {e}", exc_info=True)
                
                progress_val = ((i + 1) / total_files) * 100
                self.queue.put({"type": "progress", "value": progress_val})

        self.queue.put({
            "type": "done",
            "matched_files": matched_files,
            "cache_key": cache_key
        })

    def process_file(self, args):
        file_path, keyword, match_type = args
        try:
            if not os.path.exists(file_path):
                return False
                
            ext_lower = file_path.lower()
            if not ext_lower.endswith(('.jpg', '.jpeg', '.png', '.tiff')):
                return False

            exclude_negative = not self.include_negative_var.get()
            normalized_text = self.get_normalized_metadata(file_path, exclude_negative=exclude_negative)

            if match_type == "exact":
                return keyword == normalized_text
            else:
                tokens = keyword.split()
                if not tokens: return True
                
                if self.and_search_var.get():
                    return all(token.lower() in normalized_text.lower() for token in tokens)
                else:
                    return any(token.lower() in normalized_text.lower() for token in tokens)
        except Exception as e:
            logging.error(f"ファイル処理スレッド内例外: {file_path} -> {e}", exc_info=True)
            return False

    # ▼▼▼【機能移植】▼▼▼
    # 検索精度を向上させるため、v1.10の優れたロジックを復元
    def get_normalized_metadata(self, file_path, exclude_negative=True):
        try:
            last_modified = os.path.getmtime(file_path)
        except Exception:
            return ""

        with self.metadata_cache_lock:
            cache_entry = self.metadata_cache.get(file_path)
            if cache_entry and cache_entry.get("mtime") == last_modified:
                if exclude_negative and "meta_no_neg" in cache_entry:
                    return cache_entry["meta_no_neg"]
                if not exclude_negative and "meta" in cache_entry:
                    return cache_entry["meta"]

        raw_meta = self.get_raw_metadata(file_path)
        width, height = 0, 0
        try:
            with Image.open(file_path) as img:
                width, height = img.size
        except Exception:
            pass
        
        filtered_text = ""
        if exclude_negative:
            # v1.10のロジックを適用
            prompt_match = re.search(r'"prompt"\s*:\s*"([^"]*)"', raw_meta)
            prompt_value = prompt_match.group(1) if prompt_match else ""
            
            base_match = re.search(r'"base_caption"\s*:\s*"([^"]*)"', raw_meta, re.IGNORECASE | re.DOTALL)
            base_value = base_match.group(1) if base_match else ""
            
            char_matches = re.findall(r'"char_caption"\s*:\s*"([^"]*)"', raw_meta, re.IGNORECASE)
            joined_char_captions = " ".join(char_matches)

            # A1111形式のプロンプトも考慮
            a1111_prompt_match = re.search(r'^(.*?)\nNegative prompt: ', raw_meta, re.DOTALL)
            a1111_prompt_value = a1111_prompt_match.group(1).strip() if a1111_prompt_match else ""

            # すべてを結合して検索対象とする
            filtered_text = " ".join(filter(None, [a1111_prompt_value, prompt_value, base_value, joined_char_captions])).strip()

        with self.metadata_cache_lock:
            self.metadata_cache[file_path] = {
                "mtime": last_modified,
                "meta": raw_meta,
                "meta_no_neg": filtered_text if exclude_negative else "",
                "width": width,
                "height": height
            }
            self.queue.put({"type": "cache_update"})

        return filtered_text if exclude_negative else raw_meta

    def extract_exif_text(self, file_path):
        try:
            with open(file_path, 'rb') as f:
                tags = exifread.process_file(f, details=False)
            return "\n".join(str(v) for v in tags.values())
        except Exception:
            return ""
    
    def extract_png_text(self, file_path):
        try:
            with Image.open(file_path) as img:
                return "\n".join(str(v) for v in img.info.values())
        except Exception:
            return ""

    def match_keyword(self, keyword, match_type, text, file_path=None):
        if not text: return False
        tokens = keyword.split()
        if not tokens: return True

        target_text = text
        if not self.include_negative_var.get() and file_path:
             target_text = self.get_normalized_metadata(file_path, exclude_negative=True)
        
        target_text_lower = target_text.lower()
        if match_type == "exact":
            return keyword.lower() == target_text_lower

        if self.and_search_var.get():
            return all(token.lower() in target_text_lower for token in tokens)
        else:
            return any(token.lower() in target_text_lower for token in tokens)

    # =====================================
    # 表示・レイアウト
    # =====================================

    def layout_results(self, refresh=True):
        if refresh:
            for widget in self.results_inner_frame.winfo_children():
                widget.destroy()
            self.thumbnails.clear()
            self.selected_files.clear()

        with self.current_matched_files_lock:
            sorted_files = self.apply_sort(list(self.current_matched_files))

        max_items = self.max_display_var.get() if self.max_display_var.get() > 0 else 1
        total_items = len(sorted_files)
        self.total_pages = (total_items + max_items - 1) // max_items if total_items > 0 else 1

        if self.current_page >= self.total_pages:
            self.current_page = self.total_pages - 1
        if self.current_page < 0:
            self.current_page = 0

        self.page_info_label.config(text=f"ページ {self.current_page + 1}/{self.total_pages}")
        
        start_index = self.current_page * max_items
        end_index = start_index + max_items
        page_files = sorted_files[start_index:end_index]

        if not page_files and refresh:
            no_result_label = ttk.Label(self.results_inner_frame, text="見つかりませんでした…(>_<)")
            no_result_label.pack(padx=10, pady=10)
            self.on_frame_configure(None)
            return

        frame_width = self.results_frame.winfo_width()
        cell_width = self.thumb_size[0] + 20
        col_count = max(1, frame_width // cell_width)

        if not refresh and self.results_inner_frame.winfo_children():
            num_widgets = len(self.results_inner_frame.winfo_children())
            row_idx = num_widgets // col_count
            col_idx = num_widgets % col_count
        else:
            row_idx, col_idx = 0, 0
            
        for i, file_path in enumerate(page_files):
            if file_path in self.selected_files and not refresh: continue

            item_frame = ttk.Frame(self.results_inner_frame)
            item_frame.grid(row=row_idx, column=col_idx, padx=10, pady=10, sticky="n")
            
            var = tk.BooleanVar(value=False)
            self.selected_files[file_path] = var
            
            cbtn = ttk.Checkbutton(item_frame, variable=var)
            cbtn.pack(anchor=tk.W)

            try:
                img = Image.open(file_path)
                img.thumbnail(self.thumb_size, LANCZOS_RESAMPLING)
                thumb = ImageTk.PhotoImage(img)
                self.thumbnails.append(thumb)
                
                img_label = ttk.Label(item_frame, image=thumb, cursor="hand2")
                img_label.pack()
                img_label.bind("<Double-Button-1>", lambda e, p=file_path: self.show_full_image(p))
                img_label.bind("<Button-3>", lambda e, p=file_path: self.show_context_menu(e, p))
                img_label.bind("<Button-1>", lambda e, v=var: self.toggle_selection(v))

                try:
                    rel_path = os.path.relpath(file_path, self.dir_path_var.get())
                except ValueError:
                    rel_path = os.path.basename(file_path)

                fn_label = ttk.Label(item_frame, text=rel_path, wraplength=self.thumb_size[0])
                fn_label.pack(fill=tk.X, expand=True)

                col_idx += 1
                if col_idx >= col_count:
                    col_idx = 0
                    row_idx += 1

            except Exception as e:
                logging.error(f"サムネ生成エラー: {file_path} -> {e}")
                error_label = ttk.Label(item_frame, text="読込エラー", width=20)
                error_label.pack()
        
        self.root.update_idletasks()
        self.on_frame_configure(None)
        if refresh:
            self.canvas.yview_moveto(0)


    def apply_sort(self, file_list):
        mode = self.sort_var.get()
        try:
            if mode == "ファイル名昇順":
                return sorted(file_list, key=lambda x: os.path.basename(x).lower())
            elif mode == "ファイル名降順":
                return sorted(file_list, key=lambda x: os.path.basename(x).lower(), reverse=True)
            elif mode == "更新日時昇順":
                return sorted(file_list, key=os.path.getmtime)
            elif mode == "更新日時降順":
                return sorted(file_list, key=os.path.getmtime, reverse=True)
            elif mode == "解像度(昇順)":
                return sorted(file_list, key=self.get_resolution)
            elif mode == "解像度(降順)":
                return sorted(file_list, key=self.get_resolution, reverse=True)
            else:
                return file_list
        except FileNotFoundError:
            messagebox.showwarning("注意", "一部のファイルが見つからなかったため、再検索します。")
            self.search_images()
            return []
            
    def get_resolution(self, file_path):
        with self.metadata_cache_lock:
            cache = self.metadata_cache.get(file_path)
            if cache and "width" in cache and "height" in cache:
                return cache["width"] * cache["height"]
        return 0 

    # =====================================
    # ページング・選択
    # =====================================
    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.layout_results()

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.layout_results()

    def jump_to_page(self):
        try:
            page_num = int(self.page_jump_var.get())
            if 1 <= page_num <= self.total_pages:
                self.current_page = page_num - 1
                self.layout_results()
            else:
                messagebox.showerror("エラー", f"1から{self.total_pages}の間のページ番号を入力してください。")
        except ValueError:
            messagebox.showerror("エラー", "有効なページ番号（数値）を入力してください。")
        finally:
            self.page_jump_var.set("")
            
    def toggle_selection(self, var):
        var.set(not var.get())

    def select_all_files(self):
        for var in self.selected_files.values():
            var.set(True)

    def deselect_all_files(self):
        for var in self.selected_files.values():
            var.set(False)

    # =====================================
    # ファイル操作・ブラウジング
    # =====================================
    def browse_directory(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.dir_path_var.set(dir_path)
            self.start_directory_watch(dir_path)

    def browse_dest_directory(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.dest_path_var.set(dir_path)

    def start_directory_watch(self, directory):
        if self.observer:
            self.observer.stop()
            self.observer.join()
        if not os.path.isdir(directory): return
        
        event_handler = NewFileHandler(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, directory, recursive=self.recursive_search_var.get())
        self.observer.start()

    def copy_selected_files(self):
        if self._file_operation(shutil.copy2, "コピー"):
            dest = self.dest_path_var.get()
            if messagebox.askyesno("完了", f"コピーが完了しました。\n保存先フォルダ「{os.path.basename(dest)}」を開きますか？"):
                 self.open_folder(dest)

    def move_selected_files(self):
        if self._file_operation(shutil.move, "移動"):
            self.search_images()

    def _file_operation(self, func, op_name):
        dest = self.dest_path_var.get()
        if not dest or not os.path.isdir(dest):
            messagebox.showerror("エラー", "有効な保存先フォルダを選択してください。")
            return False
        selected_list = [f for f, var in self.selected_files.items() if var.get()]
        if not selected_list:
            messagebox.showinfo("情報", f"{op_name}対象が選択されていません。")
            return False
        
        errors = []
        for file_path in selected_list:
            try:
                if os.path.exists(file_path):
                    func(file_path, dest)
                else:
                    errors.append(f"{os.path.basename(file_path)}: 見つかりません")
            except Exception as e:
                errors.append(f"{os.path.basename(file_path)}: {e}")
        
        if errors:
            messagebox.showerror("エラー", f"{op_name}中にエラーが発生しました:\n" + "\n".join(errors))
            return False
        else:
            return True

    def rename_file(self, file_path):
        if not os.path.exists(file_path):
            messagebox.showerror("エラー", "ファイルが見つかりません。リストを更新します。")
            self.search_images()
            return
            
        current_name = os.path.basename(file_path)
        new_name = simpledialog.askstring("名前変更", "新しいファイル名:", initialvalue=current_name)
        if new_name and new_name != current_name:
            new_path = os.path.join(os.path.dirname(file_path), new_name)
            try:
                os.rename(file_path, new_path)
                messagebox.showinfo("完了", "ファイル名を変更しました。")
                self.search_images()
            except Exception as e:
                messagebox.showerror("エラー", f"名前変更失敗: {e}")
    
    def open_folder(self, folder_path):
        try:
            if os.name == 'nt':
                subprocess.Popen(['explorer', os.path.normpath(folder_path)])
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder_path])
            else:
                subprocess.Popen(['xdg-open', folder_path])
        except Exception as e:
            messagebox.showerror("エラー", f"フォルダを開けませんでした: {e}")
            
    # =====================================
    # メタデータ・プロンプト関連
    # =====================================

    def show_full_image(self, file_path):
        with self.current_matched_files_lock:
            sorted_files = self.apply_sort(list(self.current_matched_files))
        
        try:
            start_index = sorted_files.index(file_path)
            ImageViewerWindow(self.root, self, sorted_files, start_index)
        except ValueError:
             messagebox.showerror("エラー", "ファイルがリストに見つかりません。")

    # ▼▼▼【機能移植】▼▼▼
    # v1.10の優れた右クリックメニューのロジックを復元
    def show_context_menu(self, event, file_path):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="全ファイル選択", command=self.select_all_files)
        menu.add_command(label="全選択解除", command=self.deselect_all_files)
        menu.add_separator()
        menu.add_command(label="ファイル名コピー", command=lambda: self.copy_to_clipboard(os.path.basename(file_path), "ファイル名"))
        menu.add_command(label="名前変更", command=lambda: self.rename_file(file_path))
        menu.add_separator()
        menu.add_command(label="メタ情報表示", command=lambda: self.show_metadata(file_path))
        menu.add_command(label="フォルダを開く", command=lambda: self.open_folder(os.path.dirname(file_path)))
        menu.add_separator()

        # プロンプト関連のメニューを作成
        prompt_menu = tk.Menu(menu, tearoff=0)
        prompt_menu.add_command(label="ベースプロンプトをコピー", command=lambda: self.copy_base_caption(file_path))
        
        meta_text = self.get_raw_metadata(file_path)
        json_str_v4 = self.extract_json_block(meta_text, '"v4_prompt"')
        if json_str_v4:
            try:
                data_char = json.loads(json_str_v4)
                char_captions = data_char.get("caption", {}).get("char_captions", [])
                if char_captions:
                    char_sub_menu = tk.Menu(prompt_menu, tearoff=0)
                    for idx, c_dict in enumerate(char_captions):
                        c_text = c_dict.get("char_caption", "")
                        preview = c_text.strip()[:15] + ("…" if len(c_text.strip()) > 15 else "")
                        char_sub_menu.add_command(
                            label=f"キャラクタープロンプト {idx+1}: {preview}",
                            command=lambda i=idx: self.copy_char_caption(file_path, i)
                        )
                    prompt_menu.add_cascade(label="キャラクタープロンプトをコピー", menu=char_sub_menu)
            except json.JSONDecodeError:
                pass
        menu.add_cascade(label="プロンプト関連", menu=prompt_menu)

        # ネガティブプロンプト関連のメニューを作成
        negative_menu = tk.Menu(menu, tearoff=0)
        negative_menu.add_command(label="ベースネガティブをコピー", command=lambda: self.copy_base_negative(file_path))
        
        json_str_neg = self.extract_json_block(meta_text, '"v4_negative_prompt"')
        if json_str_neg:
            try:
                data_neg = json.loads(json_str_neg)
                char_negatives = data_neg.get("caption", {}).get("char_captions", [])
                if char_negatives:
                    neg_sub_menu = tk.Menu(negative_menu, tearoff=0)
                    for idx, n_dict in enumerate(char_negatives):
                        n_text = n_dict.get("char_caption", "")
                        preview = n_text.strip()[:15] + ("…" if len(n_text.strip()) > 15 else "")
                        neg_sub_menu.add_command(
                            label=f"キャラクターネガティブ {idx+1}: {preview}",
                            command=lambda i=idx: self.copy_char_negative(file_path, i)
                        )
                    negative_menu.add_cascade(label="キャラクターネガティブをコピー", menu=neg_sub_menu)
            except json.JSONDecodeError:
                pass
        menu.add_cascade(label="ネガティブ関連", menu=negative_menu)
        
        menu.tk_popup(event.x_root, event.y_root)

    def show_metadata(self, file_path):
        meta_win = tk.Toplevel(self.root)
        meta_win.title(f"メタ情報: {os.path.basename(file_path)}")
        meta_win.geometry("600x500")

        text_frame = ttk.Frame(meta_win)
        text_frame.pack(fill="both", expand=True, padx=10, pady=10)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text_widget = tk.Text(text_frame, wrap="word", yscrollcommand=scrollbar.set)
        text_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar.config(command=text_widget.yview)

        metadata_text = self.get_raw_metadata(file_path)
        if not metadata_text:
            metadata_text = "メタ情報が見つかりませんでした。"
        text_widget.insert("1.0", metadata_text)
        text_widget.config(state="disabled")
        
        button_frame = ttk.Frame(meta_win)
        button_frame.pack(pady=5)
        
        copy_all_btn = ttk.Button(button_frame, text="全文コピー", command=lambda: self.copy_to_clipboard(metadata_text, "メタ情報"))
        copy_all_btn.pack(side=tk.LEFT, padx=5)

    def get_raw_metadata(self, file_path):
        with self.metadata_cache_lock:
            cache = self.metadata_cache.get(file_path)
            if cache and "meta" in cache:
                return cache["meta"]
        
        ext_lower = file_path.lower()
        if ext_lower.endswith(('.jpg', '.jpeg', '.tiff')):
            return self.extract_exif_text(file_path)
        elif ext_lower.endswith('.png'):
            return self.extract_png_text(file_path)
        return ""

    def copy_to_clipboard(self, text, data_type):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            messagebox.showinfo("情報", f"{data_type}をコピーしました。")
        except Exception as e:
            messagebox.showerror("エラー", f"コピーに失敗しました: {e}")

    def load_image_prompt(self):
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.jpg *.jpeg *.png *.tiff")])
        if not file_path: return

        meta_text = self.get_raw_metadata(file_path)
        # A1111 style prompt
        prompt_match = re.search(r'^(.*?)\nNegative prompt: ', meta_text, re.DOTALL)
        if prompt_match:
            prompt = prompt_match.group(1).strip()
            self.keyword_var.set(prompt)
            messagebox.showinfo("情報", "抽出したプロンプトを検索キーワードに設定しました。")
        else:
            self.keyword_var.set(meta_text)
            messagebox.showwarning("警告", "複雑なプロンプトは見つかりませんでした。メタ情報全体をキーワードに設定します。")
            
    def show_novel_ai_images(self):
        directory = self.dir_path_var.get()
        if not directory or not os.path.isdir(directory):
            messagebox.showerror("エラー", "有効なフォルダを指定してください。")
            return
        
        count = self.novel_ai_count_var.get()
        if count <= 0:
            messagebox.showerror("エラー", "表示件数は1以上にしてください。")
            return

        novel_ai_files = []
        norm_dir = os.path.normpath(directory)
        with self.metadata_cache_lock:
            for file_path, data in self.metadata_cache.items():
                if "NovelAI" in data.get("meta", ""):
                    try:
                        if os.path.commonpath([file_path, norm_dir]) == norm_dir:
                            novel_ai_files.append(file_path)
                    except ValueError:
                        continue
        
        novel_ai_files.sort(key=os.path.getmtime, reverse=True)
        
        with self.current_matched_files_lock:
            self.current_matched_files = novel_ai_files[:count]

        self.layout_results()
        
class ImageViewerWindow(tk.Toplevel):
    def __init__(self, parent, app, file_list, start_index=0):
        super().__init__(parent)
        self.app = app 
        self.file_list = file_list
        self.current_index = start_index
        self.current_zoom = 1.0
        self.pil_image = None
        
        self.title("画像ビューア")
        self.geometry("1200x800")
        self.configure(bg="gray20")
        
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        btn_frame = ttk.Frame(self, style="TFrame")
        btn_frame.grid(row=0, column=0, sticky="ew", pady=5, padx=5)

        prev_btn = ttk.Button(btn_frame, text="← 前へ", command=self.prev_image)
        prev_btn.pack(side=tk.LEFT, padx=5)
        next_btn = ttk.Button(btn_frame, text="次へ →", command=self.next_image)
        next_btn.pack(side=tk.LEFT, padx=5)
        zoom_in_btn = ttk.Button(btn_frame, text="拡大 (+)", command=lambda: self.zoom(1.2))
        zoom_in_btn.pack(side=tk.LEFT, padx=5)
        zoom_out_btn = ttk.Button(btn_frame, text="縮小 (-)", command=lambda: self.zoom(0.8))
        zoom_out_btn.pack(side=tk.LEFT, padx=5)
        fit_btn = ttk.Button(btn_frame, text="フィット", command=self.fit_to_screen)
        fit_btn.pack(side=tk.LEFT, padx=5)
        original_btn = ttk.Button(btn_frame, text="原寸", command=self.original_size)
        original_btn.pack(side=tk.LEFT, padx=5)

        canvas_frame = ttk.Frame(self)
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)
        
        self.canvas = tk.Canvas(canvas_frame, bg="gray20", highlightthickness=0)
        self.hbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.vbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.config(xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)

        self.hbar.grid(row=1, column=0, sticky="ew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        
        self.bind("<Left>", lambda e: self.prev_image())
        self.bind("<Right>", lambda e: self.next_image())
        self.bind("<Control-equal>", lambda e: self.zoom(1.2))
        self.bind("<Control-minus>", lambda e: self.zoom(0.8))
        self.bind("<Control-0>", lambda e: self.fit_to_screen())
        self.canvas.bind("<Button-3>", self.show_viewer_context_menu)
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.bind("<Configure>", self.on_window_resize)
        self.resize_timer = None

        self.load_and_display_image()
        self.focus_set()

    def load_and_display_image(self):
        if not (0 <= self.current_index < len(self.file_list)):
            self.destroy()
            return
            
        file_path = self.file_list[self.current_index]
        self.title(f"画像ビューア - {os.path.basename(file_path)}")
        try:
            self.pil_image = Image.open(file_path)
            self.fit_to_screen()
        except Exception as e:
            logging.error(f"画像を開けませんでした: {file_path}, {e}")
            self.canvas.delete("all")
            self.canvas.create_text(self.winfo_width()/2, self.winfo_height()/2, 
                                    text=f"画像を開けませんでした\n{e}", fill="white", font=("", 16))

    def display_image(self):
        if not self.pil_image: return
        
        width = int(self.pil_image.width * self.current_zoom)
        height = int(self.pil_image.height * self.current_zoom)
        
        if width <= 0 or height <= 0: return

        resized_img = self.pil_image.resize((width, height), LANCZOS_RESAMPLING)
        self.tk_image = ImageTk.PhotoImage(resized_img)
        
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        self.canvas.config(scrollregion=self.canvas.bbox("all"))

    def zoom(self, factor):
        self.current_zoom = max(0.01, min(self.current_zoom * factor, 10.0))
        self.display_image()

    def fit_to_screen(self, event=None):
        if not self.pil_image: return

        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        if canvas_w < 50 or canvas_h < 50:
            self.after(50, self.fit_to_screen)
            return

        img_w, img_h = self.pil_image.size
        if img_w == 0 or img_h == 0: return

        zoom_w = (canvas_w - 10) / img_w
        zoom_h = (canvas_h - 10) / img_h
        self.current_zoom = min(zoom_w, zoom_h)
        self.display_image()
        
    def original_size(self):
        self.current_zoom = 1.0
        self.display_image()

    def prev_image(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_and_display_image()

    def next_image(self):
        if self.current_index < len(self.file_list) - 1:
            self.current_index += 1
            self.load_and_display_image()

    def show_viewer_context_menu(self, event):
        file_path = self.file_list[self.current_index]
        self.app.show_context_menu(event, file_path)

    def on_button_press(self, event):
        self.canvas.scan_mark(event.x, event.y)

    def on_move_press(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        
    def on_window_resize(self, event):
        if self.resize_timer:
            self.after_cancel(self.resize_timer)
        self.resize_timer = self.after(300, self.fit_to_screen)


def main():
    root = tk.Tk()
    app_width = 1600
    app_height = 900
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width // 2) - (app_width // 2)
    y = (screen_height // 2) - (app_height // 2)
    root.geometry(f"{app_width}x{app_height}+{x}+{y}")
    
    app = ImageSearchApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()