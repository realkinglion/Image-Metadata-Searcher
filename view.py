import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import os
import sys
import gc
import logging
import concurrent.futures
import io
try:
    from PIL import Image, ImageTk, ImageOps
except ImportError:
    raise

from config import AppConfig
from draggable_widgets import DraggableImageLabel, DroppableEntry

try:
    LANCZOS_RESAMPLING = Image.Resampling.LANCZOS
except AttributeError:
    LANCZOS_RESAMPLING = Image.LANCZOS

class ImageSearchView:
    def __init__(self, root, config: AppConfig):
        self.root = root; self.config = config; self.controller = None
        self.root.title("画像メタ情報検索くん v5.2 DnD Final")
        
        self.thumbnails = {}
        self.thumb_size = self.config.thumbnail_size
        self.max_thumbnails = self.config.max_thumbnails_memory
        self.selected_files_vars = {}
        self.current_page = 0
        self.total_pages = 1
        self._resize_after_id = None
        self.thumbnail_executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.config.thread_pool_size)

        self.dir_path_var = tk.StringVar(); self.keyword_var = tk.StringVar()
        self.match_type_var = tk.StringVar(value="partial"); self.and_search_var = tk.BooleanVar(value=True)
        self.include_negative_var = tk.BooleanVar(value=False); self.recursive_search_var = tk.BooleanVar(value=True)
        self.history_var = tk.StringVar(); self.history_sort_var = tk.StringVar(value="追加順")
        self.dest_path_var = tk.StringVar(); self.novel_ai_count_var = tk.IntVar(value=10)
        self.sort_var = tk.StringVar(value="更新日時降順")
        self.max_display_var = tk.IntVar(value=self.config.max_display_items)
        self.page_jump_var = tk.StringVar()

    def set_controller(self, controller): self.controller = controller
    def shutdown_executors(self): self.thumbnail_executor.shutdown(wait=False, cancel_futures=True)

    def _create_and_get_webp(self, file_path, cached_thumb_bytes):
        try:
            img_to_process = None
            if cached_thumb_bytes:
                img_to_process = Image.open(io.BytesIO(cached_thumb_bytes))
            else:
                with Image.open(file_path) as img:
                    img = ImageOps.exif_transpose(img)
                    if max(img.size) > 2000:
                        img.thumbnail((1000, 1000), LANCZOS_RESAMPLING)
                    img.thumbnail(self.thumb_size, LANCZOS_RESAMPLING)
                    if img.mode != 'RGB': img = img.convert('RGB')
                    img_to_process = img.copy()
            
            webp_buffer = io.BytesIO()
            img_to_process.save(webp_buffer, format="WEBP", quality=85)
            webp_bytes_to_cache = webp_buffer.getvalue() if not cached_thumb_bytes else None
            return ImageTk.PhotoImage(img_to_process), webp_bytes_to_cache
        except Exception as e:
            logging.error(f"サムネイル生成/キャッシュエラー: {file_path} -> {e}")
            return None, None
    
    def _update_thumbnail(self, future, file_path, label):
        if not label.winfo_exists(): return
        try:
            tk_thumb, webp_bytes = future.result()
            if tk_thumb:
                self.thumbnails[file_path] = tk_thumb
                label.configure(image=tk_thumb)
                
                if isinstance(label, DraggableImageLabel):
                    label.current_photo_image = tk_thumb
                
                if webp_bytes and self.config.enable_thumbnail_caching:
                    self.controller.cache_thumbnail(file_path, webp_bytes)
        except concurrent.futures.CancelledError: 
            pass
        except Exception as e:
            if label.winfo_exists(): label.configure(text="表示エラー")
            logging.error(f"サムネイルUI更新エラー: {file_path}, {e}", exc_info=True)

    def _clear_offscreen_thumbnails(self, visible_files_set):
        if len(self.thumbnails) <= self.max_thumbnails: return
        to_delete = [path for path in self.thumbnails if path not in visible_files_set]
        for path in to_delete:
            del self.thumbnails[path]
        if to_delete: gc.collect()

    def layout_results(self, files_with_thumbs, refresh=True):
        for widget in self.results_inner_frame.winfo_children(): widget.destroy()
        if refresh: self.selected_files_vars.clear()

        max_items = self.max_display_var.get();
        if max_items <= 0: max_items = 1
        total_items = len(files_with_thumbs)
        self.total_pages = (total_items + max_items - 1) // max_items if total_items > 0 else 1

        if self.current_page >= self.total_pages: self.current_page = max(0, self.total_pages - 1)
        
        self.page_info_label.config(text=f"ページ {self.current_page + 1}/{self.total_pages} ({total_items}件)")
        
        start_index, end_index = self.current_page * max_items, (self.current_page + 1) * max_items
        page_files_with_thumb_data = files_with_thumbs[start_index:end_index]
        
        self._clear_offscreen_thumbnails({path for path, _ in page_files_with_thumb_data})

        if not page_files_with_thumb_data and refresh:
            ttk.Label(self.results_inner_frame, text="見つかりませんでした…(>_<)").grid(padx=10, pady=10); return

        frame_width = self.results_frame.winfo_width(); cell_width = self.thumb_size[0] + 20
        col_count = max(1, frame_width // cell_width)
        
        for i, (file_path, cached_thumb_bytes) in enumerate(page_files_with_thumb_data):
            row_idx, col_idx = divmod(i, col_count)
            item_frame = ttk.Frame(self.results_inner_frame)
            item_frame.grid(row=row_idx, column=col_idx, padx=10, pady=10, sticky="n")
            
            if file_path not in self.selected_files_vars: self.selected_files_vars[file_path] = tk.BooleanVar(value=False)
            var = self.selected_files_vars[file_path]
            
            ttk.Checkbutton(item_frame, variable=var, style="Large.TCheckbutton").pack(anchor=tk.W)
            
            img_label = DraggableImageLabel(
                item_frame,
                self.controller,
                file_path,
                text="読込中...",
                cursor="hand2"
            )
            img_label.pack()

            if file_path in self.thumbnails:
                 tk_thumb = self.thumbnails[file_path]
                 img_label.configure(image=tk_thumb)
                 img_label.current_photo_image = tk_thumb
            else:
                future = self.thumbnail_executor.submit(self._create_and_get_webp, file_path, cached_thumb_bytes)
                future.add_done_callback(lambda f, p=file_path, l=img_label: self.root.after_idle(self._update_thumbnail, f, p, l))

            img_label.bind("<Double-Button-1>", lambda e, p=file_path: self.controller.show_full_image(p))
            img_label.bind("<Button-3>", lambda e, p=file_path: self.show_context_menu(e, p))
            
            try: rel_path = os.path.relpath(file_path, self.dir_path_var.get())
            except ValueError: rel_path = os.path.basename(file_path)
            ttk.Label(item_frame, text=rel_path, wraplength=self.thumb_size[0]).pack(fill=tk.X, expand=True)

        self.root.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if refresh: self.canvas.yview_moveto(0)

    def create_widgets(self):
        style = ttk.Style()
        style.configure("TFrame", padding=5); style.configure("TLabel", padding=3)
        style.configure("TButton", padding=3); style.configure("TLabelframe.Label", padding=3)
        style.configure("Large.TCheckbutton", padding=3)

        self.root.rowconfigure(0, weight=1); self.root.columnconfigure(0, weight=1)
        main_frame = ttk.Frame(self.root, padding="10"); main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.rowconfigure(2, weight=1); main_frame.columnconfigure(0, weight=1)
        
        top_controls_frame = ttk.Frame(main_frame); top_controls_frame.grid(row=0, column=0, sticky="ew")
        top_controls_frame.columnconfigure(1, weight=1)
        search_settings_frame = ttk.Labelframe(top_controls_frame, text="検索設定"); search_settings_frame.grid(row=0, column=0, padx=5, pady=5, sticky="ns")
        ttk.Label(search_settings_frame, text="検索対象フォルダ:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.dir_path_entry = ttk.Entry(search_settings_frame, textvariable=self.dir_path_var, width=50); self.dir_path_entry.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="ew"); self.add_context_menu(self.dir_path_entry)
        ttk.Button(search_settings_frame, text="フォルダ選択", command=self.controller.browse_directory).grid(row=1, column=2, padx=5, pady=5, sticky=tk.W)
        ttk.Label(search_settings_frame, text="検索キーワード:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
        keyword_entry = ttk.Entry(search_settings_frame, textvariable=self.keyword_var, width=50); keyword_entry.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="ew"); keyword_entry.bind("<Return>", self.controller.start_search); self.add_context_menu(keyword_entry)
        ttk.Button(search_settings_frame, text="画像から読込", command=self.controller.load_image_prompt).grid(row=3, column=2, padx=5, pady=5, sticky=tk.W)
        options_frame = ttk.Frame(search_settings_frame); options_frame.grid(row=4, column=0, columnspan=3, sticky="w", padx=5, pady=5)
        ttk.Radiobutton(options_frame, text="一部一致", variable=self.match_type_var, value="partial").grid(row=0, column=0, padx=(0,5))
        ttk.Radiobutton(options_frame, text="完全一致", variable=self.match_type_var, value="exact").grid(row=0, column=1, padx=5)
        ttk.Checkbutton(options_frame, text="AND検索", variable=self.and_search_var).grid(row=0, column=2, padx=5)
        ttk.Checkbutton(options_frame, text="ネガティブ含む", variable=self.include_negative_var).grid(row=0, column=3, padx=5)
        ttk.Checkbutton(options_frame, text="サブフォルダも検索", variable=self.recursive_search_var).grid(row=0, column=4, padx=5)
        search_button_frame = ttk.Frame(search_settings_frame); search_button_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=10)
        self.search_button = tk.Button(search_button_frame, text="検索スタート", command=self.controller.start_search, bg="#007bff", fg="white", activebackground="#0056b3", activeforeground="white", font=("", 10, "bold"), relief="raised", bd=2, padx=10, pady=3); self.search_button.pack(side=tk.LEFT, padx=5)
        self.cancel_button = tk.Button(search_button_frame, text="キャンセル", command=self.controller.cancel_search, state="disabled", bg="#6c757d", fg="white", font=("", 10, "bold"), relief="raised", bd=2, padx=10, pady=3)
        self.progress = ttk.Progressbar(search_button_frame, orient="horizontal", length=300, mode="determinate"); self.progress.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.progress_label = ttk.Label(search_button_frame, text="待機中...", width=25, anchor="w"); self.progress_label.pack(side=tk.LEFT, padx=5)
        action_frame = ttk.Labelframe(top_controls_frame, text="機能"); action_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew"); action_frame.columnconfigure(1, weight=1)
        ttk.Label(action_frame, text="検索履歴:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.history_combo = ttk.Combobox(action_frame, textvariable=self.history_var, state="readonly"); self.history_combo.grid(row=0, column=1, padx=5, pady=5, sticky="ew"); self.history_combo.bind("<<ComboboxSelected>>", self.controller.on_history_selected)
        ttk.Button(action_frame, text="選択履歴削除", command=self.controller.delete_selected_history).grid(row=0, column=2, padx=5, pady=5, sticky="w")
        ttk.Label(action_frame, text="履歴ソート:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        history_sort_combobox = ttk.Combobox(action_frame, textvariable=self.history_sort_var, state="readonly", width=10, values=("追加順", "ディレクトリ順", "キーワード順")); history_sort_combobox.grid(row=1, column=1, padx=5, pady=5, sticky="w"); history_sort_combobox.bind("<<ComboboxSelected>>", lambda e: self.controller.update_history_display())
        ttk.Button(action_frame, text="お気に入り設定保存", command=self.controller.save_favorite_settings).grid(row=1, column=2, padx=5, pady=5, sticky="w")
        file_op_frame = ttk.Labelframe(action_frame, text="選択したファイルを..."); file_op_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=5, pady=5); file_op_frame.columnconfigure(1, weight=1)
        ttk.Label(file_op_frame, text="保存先フォルダ:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        dest_entry = DroppableEntry(file_op_frame, self.controller, textvariable=self.dest_path_var)
        dest_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.add_context_menu(dest_entry)
        ttk.Button(file_op_frame, text="保存先選択", command=self.controller.browse_dest_directory).grid(row=0, column=2, padx=5, pady=5)
        ttk.Button(file_op_frame, text="コピー", command=self.controller.copy_selected_files).grid(row=0, column=3, padx=5, pady=5)
        ttk.Button(file_op_frame, text="移動", command=self.controller.move_selected_files).grid(row=0, column=4, padx=5, pady=5)
        novel_ai_frame = ttk.Frame(action_frame); novel_ai_frame.grid(row=3, column=0, columnspan=3, sticky="w", padx=5, pady=5)
        ttk.Label(novel_ai_frame, text="最新NAI画像表示件数:").pack(side=tk.LEFT)
        novel_ai_count_entry = ttk.Entry(novel_ai_frame, textvariable=self.novel_ai_count_var, width=5); novel_ai_count_entry.pack(side=tk.LEFT, padx=5); self.add_context_menu(novel_ai_count_entry)
        ttk.Button(novel_ai_frame, text="最新NovelAI画像表示", command=self.controller.show_novel_ai_images).pack(side=tk.LEFT, padx=5)
        display_settings_frame = ttk.Labelframe(top_controls_frame, text="表示・ソート設定"); display_settings_frame.grid(row=0, column=2, padx=5, pady=5, sticky="ns")
        ttk.Label(display_settings_frame, text="検索結果ソート:").grid(row=0, column=0, sticky=tk.W, padx=5)
        sort_combobox = ttk.Combobox(display_settings_frame, textvariable=self.sort_var, state="readonly", width=15, values=("ファイル名昇順", "ファイル名降順", "更新日時昇順", "更新日時降順", "解像度(昇順)", "解像度(降順)")); sort_combobox.grid(row=1, column=0, sticky=tk.W, padx=5, pady=2); sort_combobox.bind("<<ComboboxSelected>>", self.controller.on_sort_changed)
        ttk.Label(display_settings_frame, text="最大表示件数:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=(10,0))
        max_display_entry = ttk.Entry(display_settings_frame, textvariable=self.max_display_var, width=5); max_display_entry.grid(row=3, column=0, sticky=tk.W, padx=5, pady=2); self.add_context_menu(max_display_entry)
        ttk.Button(display_settings_frame, text="すべて選択", command=self.select_all_files).grid(row=4, column=0, sticky="ew", padx=5, pady=(10,2))
        ttk.Button(display_settings_frame, text="すべて解除", command=self.deselect_all_files).grid(row=5, column=0, sticky="ew", padx=5, pady=2)
        paging_frame = ttk.Frame(display_settings_frame); paging_frame.grid(row=6, column=0, sticky="ew", padx=5, pady=10)
        self.prev_page_btn = ttk.Button(paging_frame, text="前", command=self.prev_page); self.prev_page_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.next_page_btn = ttk.Button(paging_frame, text="次", command=self.next_page); self.next_page_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.page_info_label = ttk.Label(display_settings_frame, text="ページ 1/1"); self.page_info_label.grid(row=7, column=0, pady=2)
        page_jump_frame = ttk.Frame(display_settings_frame); page_jump_frame.grid(row=8, column=0, sticky="ew", padx=5, pady=2)
        page_jump_entry = ttk.Entry(page_jump_frame, textvariable=self.page_jump_var, width=5); page_jump_entry.pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(page_jump_frame, text="ページ移動", command=self.jump_to_page).pack(side=tk.LEFT)
        
        hint_frame = ttk.Frame(main_frame)
        hint_frame.grid(row=1, column=0, sticky="w", padx=5)
        ttk.Label(hint_frame, text="（Ctrl+クリックで複数選択）", font=("", 8, "italic")).pack(side=tk.LEFT)

        self.results_frame = ttk.Frame(main_frame, borderwidth=2, relief="sunken")
        self.results_frame.grid(row=2, column=0, padx=5, pady=5, sticky="nsew")
        self.results_frame.rowconfigure(0, weight=1); self.results_frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.results_frame); self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self.results_frame, orient="vertical", command=self.canvas.yview); self.scrollbar.grid(row=0, column=1, sticky="ns"); self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.results_inner_frame = ttk.Frame(self.canvas); self.canvas.create_window((0, 0), window=self.results_inner_frame, anchor="nw")
        self.results_inner_frame.bind("<Configure>", self.on_frame_configure); self.canvas.bind("<Enter>", self._bind_mousewheel); self.canvas.bind("<Leave>", self._unbind_mousewheel)
        self.results_frame.bind("<Configure>", self.on_resize_frame); self.root.protocol("WM_DELETE_WINDOW", self.controller.on_closing)
    
    def update_progress(self, value, text=""):
        self.progress["value"] = value
        if text: self.progress_label.config(text=text)
        elif 0 < value < 100: self.progress_label.config(text=f"検索中... {int(value)}%")
        elif value >= 100 and not text: self.progress_label.config(text="完了")
        elif value == 0 and not text: self.progress_label.config(text="待機中...")
    
    def get_search_parameters(self): return { "dir_path": self.dir_path_var.get(), "keyword": self.keyword_var.get(), "match_type": self.match_type_var.get(), "and_search": self.and_search_var.get(), "include_negative": self.include_negative_var.get(), "recursive_search": self.recursive_search_var.get(), }
    def get_selected_files(self): return [f for f, var in self.selected_files_vars.items() if var.get()]
    def show_context_menu(self, event, file_path):
        menu = tk.Menu(self.root, tearoff=0); menu.add_command(label="全ファイル選択", command=self.select_all_files); menu.add_command(label="全選択解除", command=self.deselect_all_files); menu.add_separator()
        menu.add_command(label="ファイル名コピー", command=lambda: self.controller.copy_to_clipboard(os.path.basename(file_path), "ファイル名"))
        menu.add_command(label="名前変更", command=lambda: self.controller.rename_file(file_path)); menu.add_separator()
        menu.add_command(label="メタ情報表示", command=lambda: self.controller.show_metadata(file_path))
        menu.add_command(label="フォルダを開く", command=lambda: self.controller.open_folder(os.path.dirname(file_path))); menu.add_separator()
        prompt_menu = tk.Menu(menu, tearoff=0); prompt_menu.add_command(label="ベースプロンプトをコピー", command=lambda: self.controller.copy_base_caption(file_path))
        char_captions = self.controller.get_char_captions(file_path)
        if char_captions:
            char_sub_menu = tk.Menu(prompt_menu, tearoff=0)
            for idx, c_text in enumerate(char_captions):
                preview = c_text.strip()[:15] + ("…" if len(c_text.strip()) > 15 else "")
                char_sub_menu.add_command(label=f"キャラ {idx+1}: {preview}", command=lambda i=idx: self.controller.copy_char_caption(file_path, i))
            prompt_menu.add_cascade(label="キャラクタープロンプト", menu=char_sub_menu)
        menu.add_cascade(label="プロンプトコピー", menu=prompt_menu)
        negative_menu = tk.Menu(menu, tearoff=0); negative_menu.add_command(label="ベースネガティブをコピー", command=lambda: self.controller.copy_base_negative(file_path))
        char_negatives = self.controller.get_char_negatives(file_path)
        if char_negatives:
            neg_sub_menu = tk.Menu(negative_menu, tearoff=0)
            for idx, n_text in enumerate(char_negatives):
                preview = n_text.strip()[:15] + ("…" if len(n_text.strip()) > 15 else "")
                neg_sub_menu.add_command(label=f"キャラ {idx+1}: {preview}", command=lambda i=idx: self.controller.copy_char_negative(file_path, i))
            negative_menu.add_cascade(label="キャラクターネガティブ", menu=neg_sub_menu)
        menu.add_cascade(label="ネガティブコピー", menu=negative_menu)
        menu.tk_popup(event.x_root, event.y_root)
    def update_history_display(self, sorted_history):
        display_list = [f"{item[0]} | {item[1]} | {item[2]}" if isinstance(item, (list, tuple)) and len(item) >= 3 else str(item) for item in sorted_history]
        self.history_combo["values"] = display_list
    def set_favorite_settings(self, settings):
        self.dir_path_var.set(settings.get("dir_path", "")); self.keyword_var.set(settings.get("keyword", ""))
        self.match_type_var.set(settings.get("match_type", "partial")); self.sort_var.set(settings.get("sort", "更新日時降順"))
        self.include_negative_var.set(settings.get("include_negative", False)); self.and_search_var.set(settings.get("and_search", True))
        self.recursive_search_var.set(settings.get("recursive_search", True)); self.max_display_var.set(settings.get("max_display", self.config.max_display_items))
        self.novel_ai_count_var.set(settings.get("novel_ai_count", 10))
    def on_frame_configure(self, event): self.canvas.configure(scrollregion=self.canvas.bbox("all"))
    def _bind_mousewheel(self, event): self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
    def _unbind_mousewheel(self, event): self.canvas.unbind_all("<MouseWheel>")
    def _on_mousewheel(self, event): self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    def on_resize_frame(self, event):
        if self._resize_after_id: self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(300, lambda: self.controller.on_sort_changed())
    def add_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0); menu.add_command(label="切り取り", command=lambda: widget.event_generate("<<Cut>>")); menu.add_command(label="コピー", command=lambda: widget.event_generate("<<Copy>>")); menu.add_command(label="ペースト", command=lambda: widget.event_generate("<<Paste>>"))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
    def prev_page(self):
        if self.current_page > 0: self.current_page -= 1; self.controller.on_sort_changed()
    def next_page(self):
        if self.current_page < self.total_pages - 1: self.current_page += 1; self.controller.on_sort_changed()
    def jump_to_page(self):
        try:
            page_num = int(self.page_jump_var.get())
            if 1 <= page_num <= self.total_pages: self.current_page = page_num - 1; self.controller.on_sort_changed()
            else: messagebox.showerror("エラー", f"1から{self.total_pages}の間のページ番号を入力してください。")
        except ValueError: messagebox.showerror("エラー", "有効なページ番号（数値）を入力してください。")
        finally: self.page_jump_var.set("")
    def select_all_files(self): [var.set(True) for var in self.selected_files_vars.values()]
    def deselect_all_files(self): [var.set(False) for var in self.selected_files_vars.values()]

class ImageViewerWindow(tk.Toplevel):
    def __init__(self, parent, controller, file_list, start_index=0):
        super().__init__(parent)
        self.controller = controller; self.file_list = file_list; self.current_index = start_index
        self.current_zoom = 1.0; self.pil_image = None; self.resize_timer = None
        self.title("画像ビューア"); self.geometry(self.controller.config.viewer_geometry); self.configure(bg="gray20")
        self.protocol("WM_DELETE_WINDOW", self.on_viewer_closing)
        self.grid_rowconfigure(1, weight=1); self.grid_columnconfigure(0, weight=1)
        btn_frame = ttk.Frame(self, style="TFrame"); btn_frame.grid(row=0, column=0, sticky="ew", pady=5, padx=5)
        ttk.Button(btn_frame, text="← 前へ", command=self.prev_image).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="次へ →", command=self.next_image).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="拡大 (+)", command=lambda: self.zoom(1.2)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="縮小 (-)", command=lambda: self.zoom(0.8)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="フィット", command=self.fit_to_screen).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="原寸", command=self.original_size).pack(side=tk.LEFT, padx=5)
        canvas_frame = ttk.Frame(self); canvas_frame.grid(row=1, column=0, sticky="nsew"); canvas_frame.grid_rowconfigure(0, weight=1); canvas_frame.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(canvas_frame, bg="gray20", highlightthickness=0)
        self.hbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview); self.vbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.config(xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)
        self.hbar.grid(row=1, column=0, sticky="ew"); self.vbar.grid(row=0, column=1, sticky="ns"); self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bind_events(); self.load_and_display_image(); self.focus_set()
    def on_viewer_closing(self): self.controller.config.viewer_geometry = self.geometry(); self.destroy()
    def bind_events(self):
        self.bind("<Left>", lambda e: self.prev_image()); self.bind("<Right>", lambda e: self.next_image()); self.bind("<Control-equal>", lambda e: self.zoom(1.2)); self.bind("<Control-plus>", lambda e: self.zoom(1.2))
        self.bind("<Control-minus>", lambda e: self.zoom(0.8)); self.bind("<Control-0>", lambda e: self.fit_to_screen()); self.canvas.bind("<Button-3>", self.show_viewer_context_menu)
        self.canvas.bind("<ButtonPress-1>", self.on_button_press); self.canvas.bind("<B1-Motion>", self.on_move_press); self.bind("<Configure>", self.on_window_resize)
    def load_and_display_image(self):
        if not (0 <= self.current_index < len(self.file_list)): self.destroy(); return
        file_path = self.file_list[self.current_index]; self.title(f"画像ビューア - {os.path.basename(file_path)}")
        try: self.pil_image = Image.open(file_path); self.fit_to_screen()
        except Exception as e:
            logging.error(f"画像を開けませんでした: {file_path}, {e}"); self.canvas.delete("all")
            self.canvas.create_text(self.winfo_width()/2, self.winfo_height()/2, text=f"画像を開けませんでした\n{e}", fill="white", font=("", 16))
    def display_image(self):
        if not self.pil_image: return
        width, height = int(self.pil_image.width * self.current_zoom), int(self.pil_image.height * self.current_zoom)
        if width <= 0 or height <= 0: return
        resized_img = self.pil_image.resize((width, height), LANCZOS_RESAMPLING)
        self.tk_image = ImageTk.PhotoImage(resized_img)
        self.canvas.delete("all"); self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image); self.canvas.config(scrollregion=self.canvas.bbox("all"))
    def zoom(self, factor): self.current_zoom = max(0.01, min(self.current_zoom * factor, 10.0)); self.display_image()
    def fit_to_screen(self, event=None):
        if not self.pil_image: return
        canvas_w, canvas_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if canvas_w < 50 or canvas_h < 50: self.after(50, self.fit_to_screen); return
        img_w, img_h = self.pil_image.size
        if img_w == 0 or img_h == 0: return
        zoom_w, zoom_h = (canvas_w - 10) / img_w, (canvas_h - 10) / img_h
        self.current_zoom = min(zoom_w, zoom_h, 1.0); self.display_image()
    def original_size(self): self.current_zoom = 1.0; self.display_image()
    def prev_image(self):
        if self.current_index > 0: self.current_index -= 1; self.load_and_display_image()
    def next_image(self):
        if self.current_index < len(self.file_list) - 1: self.current_index += 1; self.load_and_display_image()
    def show_viewer_context_menu(self, event):
        file_path = self.file_list[self.current_index]
        self.controller.view.show_context_menu(event, file_path)
    def on_button_press(self, event): self.canvas.scan_mark(event.x, event.y)
    def on_move_press(self, event): self.canvas.scan_dragto(event.x, event.y, gain=1)
    def on_window_resize(self, event):
        if self.resize_timer: self.after_cancel(self.resize_timer)
        self.resize_timer = self.after(300, self.fit_to_screen)