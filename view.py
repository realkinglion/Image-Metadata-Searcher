import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import os
import sys
import gc
import logging
import concurrent.futures
import io
import tkinterdnd2 as tkdnd
import threading

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

class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event):
        x = self.widget.winfo_rootx() + event.x + 20
        y = self.widget.winfo_rooty() + event.y + 10

        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        label = tk.Label(self.tooltip, text=self.text, justify='left',
                         background="#ffffe0", relief='solid', borderwidth=1,
                         font=("", "8", "normal"))
        label.pack(ipadx=1)

    def hide_tooltip(self, event):
        if self.tooltip:
            self.tooltip.destroy()
        self.tooltip = None

class PlaceholderEntry(ttk.Entry):
    def __init__(self, container, placeholder, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.placeholder = placeholder
        self.placeholder_color = 'grey'
        self.default_fg_color = self['foreground']
        
        self.is_placeholder_active = False

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        
        self.put_placeholder()

    def put_placeholder(self):
        self.delete(0, "end")
        self.insert(0, self.placeholder)
        self['foreground'] = self.placeholder_color
        self.is_placeholder_active = True

    def _on_focus_in(self, event):
        if self.is_placeholder_active:
            self.delete(0, "end")
            self['foreground'] = self.default_fg_color
            self.is_placeholder_active = False

    def _on_focus_out(self, event):
        if not self.get():
            self.put_placeholder()

class VisualDropZone(ttk.Frame):
    def __init__(self, parent, controller, dir_path_var):
        super().__init__(parent, style='Card.TFrame')
        self.controller = controller
        self.dir_path_var = dir_path_var
        self.trace_id = None
        self._initial_draw_done = False

        self.drop_canvas = tk.Canvas(self, height=55, bg='#fafafa', highlightthickness=2, highlightbackground='#d0d0d0', cursor="hand2")
        self.drop_canvas.pack(fill='x', expand=True, padx=10, pady=5)
        
        self.drop_target_register(tkdnd.DND_FILES)
        self.dnd_bind('<<DragEnter>>', self.on_drag_enter)
        self.dnd_bind('<<DragLeave>>', self.on_drag_leave)
        self.dnd_bind('<<Drop>>', self.on_drop)
        self.drop_canvas.bind("<Button-1>", self.on_click)
        
        self.trace_id = self.dir_path_var.trace_add('write', self._update_display)
        self.bind("<Destroy>", self._on_destroy)
        self.drop_canvas.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        if not self._initial_draw_done and event.width > 1 and event.height > 1:
            self._update_display()
            self._initial_draw_done = True
            
    def _on_destroy(self, event):
        if self.trace_id:
            self.dir_path_var.trace_remove('write', self.trace_id)
            self.trace_id = None

    def _update_display(self, *args):
        if not self.winfo_exists():
            return
            
        self.drop_canvas.delete("all")
        path = self.dir_path_var.get()

        self.drop_canvas.update_idletasks()
        cx = self.drop_canvas.winfo_width() / 2
        cy = self.drop_canvas.winfo_height() / 2

        if not path:
            self.drop_canvas.create_text(30, cy, text="📁", font=('', 24), fill='#666666', anchor='center', tags="placeholder")
            self.drop_canvas.create_text(55, cy, text="フォルダをドロップ または クリックして選択", font=('', 9), fill='#666666', justify='left', anchor='w', tags="placeholder")
            self.drop_canvas.tag_bind("placeholder", "<Button-1>", self.on_click)
        else:
            folder_name = os.path.basename(path)
            
            clear_btn_text = self.drop_canvas.create_text(self.drop_canvas.winfo_width() - 20, cy, text="×", font=("", 16, "bold"), fill='#999', anchor='center', tags="clear_btn")
            self.drop_canvas.tag_bind("clear_btn", "<Enter>", lambda e: self.drop_canvas.itemconfig(clear_btn_text, fill='red'))
            self.drop_canvas.tag_bind("clear_btn", "<Leave>", lambda e: self.drop_canvas.itemconfig(clear_btn_text, fill='#999'))
            self.drop_canvas.tag_bind("clear_btn", "<Button-1>", self._clear_path)
            
            self.drop_canvas.create_text(20, cy, text=f"📂 {folder_name}", font=("", 11), anchor='w', tags="path_display")
            
            self.drop_canvas.addtag_all("all")
            Tooltip(self.drop_canvas, path)
            
    def _clear_path(self, event=None):
        self.dir_path_var.set("")

    def on_drag_enter(self, event):
        self.drop_canvas.config(bg='#e0e8f0', highlightbackground='#007bff')
        return event.action

    def on_drag_leave(self, event):
        self.drop_canvas.config(bg='#fafafa', highlightbackground='#d0d0d0')

    def on_drop(self, event):
        self.on_drag_leave(event)
        try:
            paths = self.winfo_toplevel().tk.splitlist(event.data)
            if paths and os.path.isdir(paths[0]):
                self.controller.set_search_directory(paths[0])
            else:
                messagebox.showwarning("不正なドロップ", "フォルダをドロップしてください。")
        except Exception as e:
             messagebox.showerror("エラー", f"ドロップ処理中にエラーが発生しました: {e}")
        return event.action
    
    def on_click(self, event):
        overlapping = self.drop_canvas.find_overlapping(event.x, event.y, event.x, event.y)
        if not overlapping or "clear_btn" not in self.drop_canvas.gettags(overlapping):
            self.controller.browse_directory()

class SmartSearchBar(ttk.Frame):
    def __init__(self, parent, controller, view):
        super().__init__(parent, style='Card.TFrame')
        self.controller = controller
        self.view = view
        self._suggestion_click_pending = False
        
        # ★★★ 変更点: サジェスト機能用の変数を追加 ★★★
        self._suggestion_timer = None
        self._suggestion_cache = {}
        self._suggestion_thread = None

        self.search_frame_container = ttk.Frame(self, style='Card.TFrame')
        self.search_frame_container.pack(fill='x', expand=True, padx=10, pady=(10, 0))

        search_frame = ttk.Frame(self.search_frame_container, style='Card.TFrame')
        search_frame.pack(fill='x', expand=True)

        search_icon_label = ttk.Label(search_frame, text="🔍", font=("", 12))
        search_icon_label.pack(side='left', padx=(5, 10))

        self.search_entry = PlaceholderEntry(
            search_frame,
            "例: 1girl, blue hair, masterpiece (Enterで検索)",
            textvariable=self.view.keyword_var,
            width=60
        )
        self.search_entry.pack(side='left', fill='x', expand=True, ipady=4)
        self.view.add_context_menu(self.search_entry)
        
        self.suggestion_popup = None
        self.suggestion_listbox = None
        
        self.search_entry.bind("<Return>", self._on_search_entry_return)
        self.search_entry.bind("<KeyRelease>", self._on_key_release)
        self.search_entry.bind("<FocusOut>", self._hide_suggestions_after_delay)
        self.search_entry.bind("<Down>", self._focus_listbox)
        
        options_frame = ttk.Frame(self, style='Card.TFrame')
        options_frame.pack(fill='x', expand=True, padx=15, pady=(5, 10))

        ttk.Checkbutton(options_frame, text="AND検索", variable=self.view.and_search_var).pack(side='left', padx=5)
        ttk.Checkbutton(options_frame, text="ネガティブ含む", variable=self.view.include_negative_var).pack(side='left', padx=5)
        ttk.Checkbutton(options_frame, text="サブフォルダも検索", variable=self.view.recursive_search_var).pack(side='left', padx=5)

        ttk.Radiobutton(options_frame, text="一部一致", variable=self.view.match_type_var, value="partial").pack(side='left', padx=(15, 5))
        ttk.Radiobutton(options_frame, text="完全一致", variable=self.view.match_type_var, value="exact").pack(side='left', padx=5)
    
    def _create_suggestion_popup(self):
        if self.suggestion_popup and self.suggestion_popup.winfo_exists():
            return
        
        self.suggestion_popup = tk.Toplevel(self.search_entry)
        self.suggestion_popup.wm_overrideredirect(True)
        
        self.suggestion_listbox = tk.Listbox(
            self.suggestion_popup,
            height=5,
            background='white',
            foreground='black',
            selectbackground='#0078d4',
            selectforeground='white',
            highlightthickness=1,
            highlightbackground='#bbbbbb',
            relief='solid',
            bd=1,
            exportselection=False
        )
        self.suggestion_listbox.pack(fill='both', expand=True)
        
        self.suggestion_listbox.bind("<<ListboxSelect>>", self._on_suggestion_click)
        self.suggestion_listbox.bind("<Double-Button-1>", self._on_suggestion_double_click)
        self.suggestion_listbox.bind("<Return>", lambda e: self._apply_selected_suggestion())
        self.suggestion_listbox.bind("<Escape>", self._hide_suggestions)
        self.suggestion_listbox.bind("<Up>", self._on_listbox_up)

    def _update_suggestion_listbox(self, suggestions):
        if not self.search_entry.winfo_viewable(): return

        if suggestions:
            if not self.suggestion_popup or not self.suggestion_popup.winfo_exists():
                self._create_suggestion_popup()
            
            self.suggestion_listbox.delete(0, tk.END)
            for s in suggestions:
                self.suggestion_listbox.insert(tk.END, s)
            
            self.search_entry.update_idletasks()
            x = self.search_entry.winfo_rootx()
            y = self.search_entry.winfo_rooty() + self.search_entry.winfo_height()
            width = self.search_entry.winfo_width()
            height = min(len(suggestions), 5) * 20 + 4
            
            self.suggestion_popup.geometry(f"{width}x{height}+{x}+{y}")
            self.suggestion_popup.lift()
            self.suggestion_popup.deiconify()
        else:
            self._hide_suggestions()
            
    def _hide_suggestions(self, event=None):
        if self.suggestion_popup and self.suggestion_popup.winfo_exists():
            self.suggestion_popup.withdraw()

    def _on_search_entry_return(self, event):
        if (self.suggestion_popup and self.suggestion_popup.winfo_viewable() and 
            self.suggestion_listbox and self.suggestion_listbox.curselection()):
            self._apply_selected_suggestion()
            return "break"
        else:
            self._hide_suggestions()
            self.controller.start_search(event)
            return "break"

    def _focus_listbox(self, event=None):
        if (self.suggestion_popup and self.suggestion_popup.winfo_viewable() and 
            self.suggestion_listbox and self.suggestion_listbox.size() > 0):
            self.suggestion_listbox.focus_set()
            self.suggestion_listbox.selection_set(0)
            self.suggestion_listbox.activate(0)

    # ★★★ 変更点: _on_key_releaseを全面的に書き換え ★★★
    def _on_key_release(self, event):
        if not self.controller.config.enable_suggestions:
            return

        if event.keysym in ("Up", "Down", "Left", "Right", "Return", "Escape", "Tab", "Shift_L", "Shift_R", "Control_L", "Control_R"):
            if event.keysym == "Escape":
                self._hide_suggestions()
            return

        current_text = self.search_entry.get()
        if hasattr(self.search_entry, 'is_placeholder_active') and self.search_entry.is_placeholder_active:
            return
            
        if self._suggestion_timer:
            self.after_cancel(self._suggestion_timer)
            
        delay = self.controller.config.suggestion_delay_ms
        self._suggestion_timer = self.after(delay, lambda: self._fetch_suggestions_async(current_text))

    def _fetch_suggestions_async(self, text):
        """非同期でサジェストを取得する"""
        parts = text.split(',')
        last_part = parts[-1].strip()
        words_in_last_part = last_part.split()
        prefix = words_in_last_part[-1] if words_in_last_part else ""
        
        min_chars = self.controller.config.suggestion_min_chars
        if len(prefix) < min_chars:
            self._hide_suggestions()
            return
            
        if prefix in self._suggestion_cache:
            self._update_suggestion_listbox(self._suggestion_cache[prefix])
            return

        if self._suggestion_thread and self._suggestion_thread.is_alive():
            return
            
        self._suggestion_thread = threading.Thread(
            target=self._fetch_suggestions_worker,
            args=(text, prefix),
            daemon=True
        )
        self._suggestion_thread.start()

    def _fetch_suggestions_worker(self, text, prefix):
        """ワーカースレッドで実際にサジェストを取得する"""
        try:
            suggestions = self.controller.get_keyword_suggestions(text)
            self._suggestion_cache[prefix] = suggestions
            
            # UIスレッドで更新をスケジュールする
            self.after_idle(lambda: self._update_suggestion_listbox(suggestions))
        except Exception as e:
            logging.error(f"サジェスト取得ワーカースレッドでエラー: {e}")

    def _apply_selected_suggestion(self):
        if not self.suggestion_listbox or not self.suggestion_listbox.curselection():
            return

        selected_index = self.suggestion_listbox.curselection()[0]
        selected_suggestion = self.suggestion_listbox.get(selected_index)
        
        current_text = self.search_entry.get()
        parts = current_text.split(',')
        
        words_in_last_part = parts[-1].strip().split()
        
        if words_in_last_part:
            words_in_last_part[-1] = selected_suggestion
            parts[-1] = ' ' + ' '.join(words_in_last_part)
        else:
            parts[-1] = ' ' + selected_suggestion
        
        new_text = ','.join(parts).lstrip(' ,').rstrip()
        
        self.view.keyword_var.set(new_text)
        self.search_entry.icursor(tk.END)
        self.search_entry.focus_set()
        self._hide_suggestions()

    def _on_listbox_up(self, event):
        if not self.suggestion_listbox:
            return
        current = self.suggestion_listbox.curselection()
        if current and current[0] == 0:
            self._return_to_search_entry()
            return "break"

    def _return_to_search_entry(self):
        self.search_entry.focus_set()
        self.search_entry.icursor(tk.END)
        self._hide_suggestions()
        return "break"
        
    def _on_suggestion_click(self, event):
        self._suggestion_click_pending = True
        self.after(100, self._process_suggestion_click)

    def _on_suggestion_double_click(self, event):
        self._apply_selected_suggestion()

    def _process_suggestion_click(self):
        if self._suggestion_click_pending:
            self._apply_selected_suggestion()
            self._suggestion_click_pending = False

    def _hide_suggestions_after_delay(self, event=None):
        def check_and_hide():
            if not self._suggestion_click_pending:
                self._hide_suggestions()
        self.after(200, check_and_hide)

class ImageSearchView:
    def __init__(self, root, config: AppConfig):
        self.root = root
        self.config = config
        self.controller = None
        self.root.title("画像メタ情報検索くん v5.5 高速サジェスト版")
        
        self.thumbnails = {}
        self.thumb_size = (self.config.thumbnail_display_size, self.config.thumbnail_display_size)
        self.max_thumbnails = self.config.max_thumbnails_memory
        self.selected_files_vars = {}
        self.current_page = 0
        self.total_pages = 1
        self._resize_after_id = None
        self.thumbnail_executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.config.thread_pool_size)

        self.dir_path_var = tk.StringVar()
        self.keyword_var = tk.StringVar()
        self.match_type_var = tk.StringVar(value="partial")
        self.and_search_var = tk.BooleanVar(value=True)
        self.include_negative_var = tk.BooleanVar(value=False)
        self.recursive_search_var = tk.BooleanVar(value=True)
        self.history_var = tk.StringVar()
        self.history_sort_var = tk.StringVar(value="追加順")
        self.dest_path_var = tk.StringVar()
        self.novel_ai_count_var = tk.IntVar(value=10)
        self.sort_var = tk.StringVar(value="更新日時降順")
        self.max_display_var = tk.IntVar(value=self.config.max_display_items)
        self.page_jump_var = tk.StringVar()
        
        self.ui_mode = tk.StringVar(value=self.config.last_ui_mode)
        self.ui_mode.trace_add('write', self._update_ui_layout)
        self.thumb_size_var = tk.IntVar(value=self.config.thumbnail_display_size)
        self.thumb_size_var.trace_add('write', self._schedule_thumb_resize)
        self._thumb_resize_job = None

        self.top_controls_frame = None 
        
        self.search_entry = None
        self.history_sort_combobox = None
        self.history_combo = None
        self.search_button = None
        self.cancel_button = None
        self.progress = None
        self.progress_label = None
        self.page_info_label = None
        self.prev_page_btn = None
        self.next_page_btn = None
        
        self.button_manager_mode = None
        self.action_bar_frame = None
        self.smart_tags_frame = None
        self._selection_update_job = None
        
        self._is_updating_layout = False
        self._last_selection_count = -1

    def set_controller(self, controller):
        self.controller = controller

    def shutdown_executors(self):
        self.thumbnail_executor.shutdown(wait=False, cancel_futures=True)

    def _create_and_get_webp(self, file_path, cached_thumb_bytes):
        try:
            img_to_process = None
            if cached_thumb_bytes:
                img_to_process = Image.open(io.BytesIO(cached_thumb_bytes))
            else:
                with Image.open(file_path) as img:
                    img = ImageOps.exif_transpose(img)
                    img.thumbnail(self.config.thumbnail_cache_size, LANCZOS_RESAMPLING)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    img_to_process = img.copy()
            
            webp_buffer = io.BytesIO()
            img_to_process.save(webp_buffer, format="WEBP", quality=85)
            webp_bytes_to_cache = webp_buffer.getvalue() if not cached_thumb_bytes else None
            
            display_size = self.thumb_size_var.get()
            img_to_process.thumbnail((display_size, display_size), LANCZOS_RESAMPLING)

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

    def _on_item_enter(self, frame, var):
        if not var.get():
            frame.configure(style='Hover.TFrame')

    def _on_item_leave(self, frame, var):
        if not var.get():
            frame.configure(style='TFrame')
            
    def _update_selection_visuals(self, frame, var, *args):
        if not frame.winfo_exists():
            return

        if var.get():
            frame.configure(style='Selected.TFrame')
        else:
            frame.configure(style='TFrame')

    def _handle_selection_change(self, frame, var, *args):
        if self._is_updating_layout:
            return
        self._update_selection_visuals(frame, var)
        self.schedule_action_bar_update()

    def schedule_action_bar_update(self):
        if self._selection_update_job:
            self.root.after_cancel(self._selection_update_job)
        self._selection_update_job = self.root.after(50, self._update_contextual_actions)
    
    def layout_results(self, files_with_thumbs, refresh=True):
        self._is_updating_layout = True
        try:
            saved_selections = {path for path, var in self.selected_files_vars.items() if var.get()}
            
            for widget in self.results_inner_frame.winfo_children():
                widget.destroy()
            
            if refresh:
                self.selected_files_vars.clear()
                saved_selections.clear()
            
            self.schedule_action_bar_update()

            max_items = self.max_display_var.get()
            if max_items <= 0: max_items = 1
            total_items = len(files_with_thumbs)
            self.total_pages = (total_items + max_items - 1) // max_items if total_items > 0 else 1

            if self.current_page >= self.total_pages: self.current_page = max(0, self.total_pages - 1)
            
            if self.page_info_label:
                self.page_info_label.config(text=f"ページ {self.current_page + 1}/{self.total_pages} ({total_items}件)")
            
            start_index, end_index = self.current_page * max_items, (self.current_page + 1) * max_items
            page_files_with_thumb_data = files_with_thumbs[start_index:end_index]
            
            self._clear_offscreen_thumbnails({path for path, _ in page_files_with_thumb_data})

            if not page_files_with_thumb_data and refresh:
                ttk.Label(self.results_inner_frame, text="見つかりませんでした…(>_<)").grid(padx=10, pady=10)
                return

            display_size = self.thumb_size_var.get()
            frame_width = self.results_frame.winfo_width(); cell_width = display_size + 20
            col_count = max(1, frame_width // cell_width)
            
            for i, (file_path, cached_thumb_bytes) in enumerate(page_files_with_thumb_data):
                row_idx, col_idx = divmod(i, col_count)
                item_frame = ttk.Frame(self.results_inner_frame, style='TFrame', padding=5)
                item_frame.grid(row=row_idx, column=col_idx, padx=5, pady=5, sticky="n")
                
                initial_value = file_path in saved_selections
                var = self.selected_files_vars.get(file_path)
                if not var:
                    var = tk.BooleanVar(value=initial_value)
                    self.selected_files_vars[file_path] = var
                elif var.get() != initial_value:
                    var.set(initial_value)

                if hasattr(var, '_trace_id') and var.trace_id:
                    var.trace_remove('write', var.trace_id)
                
                callback = lambda *args, f=item_frame, v=var: self._handle_selection_change(f, v)
                var.trace_id = var.trace_add('write', callback)

                ttk.Checkbutton(item_frame, variable=var, style="Large.TCheckbutton").pack(anchor=tk.W)
                
                img_label = DraggableImageLabel(item_frame, self.controller, file_path, text="読込中...", cursor="hand2")
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
                ttk.Label(item_frame, text=rel_path, wraplength=display_size).pack(fill=tk.X, expand=True)
                
                for widget in [item_frame] + item_frame.winfo_children():
                    widget.bind("<Enter>", lambda e, f=item_frame, v=var: self._on_item_enter(f, v))
                    widget.bind("<Leave>", lambda e, f=item_frame, v=var: self._on_item_leave(f, v))

                self._update_selection_visuals(item_frame, var)
        finally:
            self._is_updating_layout = False

        self.root.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if refresh: self.canvas.yview_moveto(0)

    def create_widgets(self):
        style = ttk.Style()
        style.configure("TFrame")
        style.configure("TLabel", padding=3)
        style.configure("TButton", padding=3)
        style.configure("TLabelframe.Label", padding=3)
        style.configure("Large.TCheckbutton", padding=3)
        style.configure('Card.TFrame', background='#ffffff', relief='solid', borderwidth=1)
        style.configure('Selected.TFrame', background='#ddeeff')
        style.configure('Hover.TFrame', background='#f0f8ff')

        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.rowconfigure(4, weight=1) 
        main_frame.columnconfigure(0, weight=1)

        mode_switcher_frame = ttk.Frame(main_frame)
        mode_switcher_frame.grid(row=0, column=0, sticky="ne", padx=5)
        ttk.Label(mode_switcher_frame, text="[F1: ヘルプ]", foreground="grey").pack(side='left', padx=(0,10))
        ttk.Label(mode_switcher_frame, text="表示モード:").pack(side='left', padx=(0, 5))
        simple_rb = ttk.Radiobutton(mode_switcher_frame, text="シンプル", variable=self.ui_mode, value='simple')
        simple_rb.pack(side='left')
        Tooltip(simple_rb, "基本的な検索機能のみを表示します")
        full_rb = ttk.Radiobutton(mode_switcher_frame, text="フル", variable=self.ui_mode, value='full')
        full_rb.pack(side='left')
        Tooltip(full_rb, "すべての機能と設定を表示します")

        self.top_controls_frame = ttk.Frame(main_frame)
        self.top_controls_frame.grid(row=1, column=0, sticky="new")
        self.top_controls_frame.columnconfigure(0, weight=1)

        self.action_bar_frame = ttk.Labelframe(main_frame, text="アクション", padding=5)
        self.action_bar_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        
        self.smart_tags_frame = ttk.Labelframe(main_frame, text="絞り込みタグ", padding=5)
        self.smart_tags_frame.grid(row=3, column=0, sticky="ew", padx=5, pady=(0, 5))

        self.results_frame = ttk.Frame(main_frame, borderwidth=2, relief="sunken")
        self.results_frame.grid(row=4, column=0, padx=5, pady=5, sticky="nsew")
        self.results_frame.rowconfigure(0, weight=1)
        self.results_frame.columnconfigure(0, weight=1)
        
        self.canvas = tk.Canvas(self.results_frame)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self.results_frame, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.results_inner_frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.results_inner_frame, anchor="nw")
        
        self.results_inner_frame.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)
        self.results_frame.bind("<Configure>", self.on_resize_frame)
        self.root.protocol("WM_DELETE_WINDOW", self.controller.on_closing)

        self._update_ui_layout()
        self._update_contextual_actions()
        self.display_smart_tags([])

    def display_smart_tags(self, tags):
        for widget in self.smart_tags_frame.winfo_children():
            widget.destroy()
        
        if not tags:
            self.smart_tags_frame.grid_remove()
            return
        
        self.smart_tags_frame.grid()
        for tag, count in tags:
            btn_text = f"{tag} ({count})"
            btn = ttk.Button(self.smart_tags_frame, text=btn_text, command=lambda t=tag: self.controller.add_keyword_and_search(t))
            btn.pack(side='left', padx=2, pady=2)
            Tooltip(btn, f"現在の検索キーワードに '{tag}' を追加して再検索します")

    def _update_ui_layout(self, *args):
        for widget in self.top_controls_frame.winfo_children():
            widget.destroy()

        if self.ui_mode.get() == 'full':
            self._build_full_mode_ui()
        else:
            self._build_simple_mode_ui()
        
        self.controller.update_history_display()

    def _update_contextual_actions(self):
        current_selection_count = len(self.get_selected_files())
        if hasattr(self, '_last_selection_count') and current_selection_count == self._last_selection_count:
            return
        self._last_selection_count = current_selection_count

        for widget in self.action_bar_frame.winfo_children():
            widget.destroy()

        count = current_selection_count
        if count == 0:
            self._build_no_selection_actions()
        elif count == 1:
            self._build_single_selection_actions(self.get_selected_files()[0])
        else:
            self._build_multi_selection_actions(count)

    def _build_no_selection_actions(self):
        msg = "ファイルを選択で操作表示 (ヒント: Ctrl+クリックでも選択可)"
        label = ttk.Label(self.action_bar_frame, text=msg, justify='center', foreground='grey')
        label.pack(side='left', padx=5, pady=5)
        
        ttk.Frame(self.action_bar_frame).pack(side='left', expand=True, fill='x')

        ttk.Button(self.action_bar_frame, text="このページの全ファイルを選択", command=self.select_all_files).pack(side='left', padx=5)

    def _build_single_selection_actions(self, file_path):
        ttk.Label(self.action_bar_frame, text=f"「{os.path.basename(file_path)}」を選択中").pack(side='left', padx=5)
        ttk.Separator(self.action_bar_frame, orient='vertical').pack(side='left', fill='y', padx=10, pady=5)
        
        ttk.Button(self.action_bar_frame, text="プレビュー", command=lambda: self.controller.show_full_image(file_path)).pack(side='left', padx=5)
        ttk.Button(self.action_bar_frame, text="メタ情報", command=lambda: self.controller.show_metadata(file_path)).pack(side='left', padx=5)
        ttk.Button(self.action_bar_frame, text="フォルダを開く", command=lambda: self.controller.open_folder(os.path.dirname(file_path))).pack(side='left', padx=5)
        ttk.Button(self.action_bar_frame, text="名前変更", command=lambda: self.controller.rename_file(file_path)).pack(side='left', padx=5)
        
        ttk.Separator(self.action_bar_frame, orient='vertical').pack(side='left', fill='y', padx=10, pady=5)
        
        dest_path = self.dest_path_var.get()
        copy_state = "normal" if dest_path and os.path.isdir(dest_path) else "disabled"
        copy_btn = ttk.Button(self.action_bar_frame, text="コピー", command=self.controller.copy_selected_files, state=copy_state)
        copy_btn.pack(side='left', padx=5)
        move_btn = ttk.Button(self.action_bar_frame, text="移動", command=self.controller.move_selected_files, state=copy_state)
        move_btn.pack(side='left', padx=5)

        if copy_state == "disabled":
            Tooltip(copy_btn, "ファイル操作の前に「フル」モードで保存先を指定してください")
            Tooltip(move_btn, "ファイル操作の前に「フル」モードで保存先を指定してください")
        
        ttk.Frame(self.action_bar_frame).pack(side='left', expand=True, fill='x')
        ttk.Button(self.action_bar_frame, text="選択解除", command=self.deselect_all_files).pack(side='left', padx=5)

    def _build_multi_selection_actions(self, count):
        ttk.Label(self.action_bar_frame, text=f"{count} 件のファイルを選択中").pack(side='left', padx=5)
        
        ttk.Separator(self.action_bar_frame, orient='vertical').pack(side='left', fill='y', padx=10, pady=5)
        
        dest_path = self.dest_path_var.get()
        copy_state = "normal" if dest_path and os.path.isdir(dest_path) else "disabled"
        copy_btn = ttk.Button(self.action_bar_frame, text=f"選択した{count}件をコピー", command=self.controller.copy_selected_files, state=copy_state)
        copy_btn.pack(side='left', padx=5)
        move_btn = ttk.Button(self.action_bar_frame, text=f"選択した{count}件を移動", command=self.controller.move_selected_files, state=copy_state)
        move_btn.pack(side='left', padx=5)

        if copy_state == "disabled":
            Tooltip(copy_btn, "ファイル操作の前に「フル」モードで保存先を指定してください")
            Tooltip(move_btn, "ファイル操作の前に「フル」モードで保存先を指定してください")

        ttk.Frame(self.action_bar_frame).pack(side='left', expand=True, fill='x')
        ttk.Button(self.action_bar_frame, text="全選択解除", command=self.deselect_all_files).pack(side='left', padx=5)

    def _build_simple_mode_ui(self):
        self.button_manager_mode = 'grid'
        self.top_controls_frame.columnconfigure(0, weight=2) 
        self.top_controls_frame.columnconfigure(1, weight=1)

        drop_zone_frame = ttk.Labelframe(self.top_controls_frame, text="1. 検索フォルダ")
        drop_zone_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")
        visual_drop_zone = VisualDropZone(drop_zone_frame, self.controller, self.dir_path_var)
        visual_drop_zone.pack(fill='x', expand=True)
        
        search_bar_frame = ttk.Labelframe(self.top_controls_frame, text="2. 検索キーワード")
        search_bar_frame.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        smart_search_bar = SmartSearchBar(search_bar_frame, self.controller, self)
        smart_search_bar.pack(fill='x', expand=True)
        self.search_entry = smart_search_bar.search_entry

        history_frame = ttk.Labelframe(self.top_controls_frame, text="検索履歴")
        history_frame.grid(row=1, column=1, padx=5, pady=5, sticky="nsew")
        history_frame.columnconfigure(0, weight=1)
        self.history_combo = ttk.Combobox(history_frame, textvariable=self.history_var, state="readonly")
        self.history_combo.grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        self.history_combo.bind("<<ComboboxSelected>>", self.controller.on_history_selected)
        Tooltip(self.history_combo, "過去の検索履歴を表示・選択します")
        
        save_fav_btn_simple = ttk.Button(history_frame, text="⭐", width=3, command=self.controller.save_favorite_settings)
        save_fav_btn_simple.grid(row=0, column=1, sticky="e", padx=(0,5), pady=5)
        Tooltip(save_fav_btn_simple, "現在の検索設定をお気に入りとして保存します")
        
        action_frame = ttk.Frame(self.top_controls_frame)
        action_frame.grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        self.search_button = tk.Button(action_frame, text="検索スタート", command=self.controller.start_search, bg="#007bff", fg="white", font=("", 12, "bold"), relief="raised", bd=2, padx=15, pady=5)
        self.cancel_button = tk.Button(action_frame, text="キャンセル", command=self.controller.cancel_search, state="disabled", bg="#6c757d", fg="white", font=("", 12, "bold"), relief="raised", bd=2, padx=15, pady=5)
        self.show_search_button()

        progress_frame = ttk.Frame(action_frame)
        progress_frame.grid(row=1, column=0, sticky="ew", pady=(5,0))
        self.progress = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate")
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress_label = ttk.Label(progress_frame, text="待機中...", width=15, anchor="w")
        self.progress_label.pack(side=tk.LEFT, padx=5)
        
        self.page_info_label = ttk.Label(self.top_controls_frame) 
        
        self.setup_keyboard_navigation()

    def _build_full_mode_ui(self):
        self.button_manager_mode = 'pack'
        self.top_controls_frame.columnconfigure(0, weight=1)
        self.top_controls_frame.columnconfigure(1, weight=2)
        
        drop_zone_frame = ttk.Labelframe(self.top_controls_frame, text="1. 検索フォルダ")
        drop_zone_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        visual_drop_zone = VisualDropZone(drop_zone_frame, self.controller, self.dir_path_var)
        visual_drop_zone.pack(fill='x', expand=True)

        right_frame = ttk.Frame(self.top_controls_frame)
        right_frame.grid(row=0, column=1, rowspan=2, padx=5, pady=5, sticky="nsew")
        right_frame.columnconfigure(0, weight=1)

        search_bar_frame = ttk.Labelframe(right_frame, text="2. 検索キーワードとオプション")
        search_bar_frame.grid(row=0, column=0, sticky="ew")
        smart_search_bar = SmartSearchBar(search_bar_frame, self.controller, self)
        smart_search_bar.pack(fill='x', expand=True)
        self.search_entry = smart_search_bar.search_entry

        search_button_frame = ttk.Frame(right_frame)
        search_button_frame.grid(row=1, column=0, sticky="ew", pady=5)
        self.search_button = tk.Button(search_button_frame, text="検索スタート", command=self.controller.start_search, bg="#007bff", fg="white", activebackground="#0056b3", activeforeground="white", font=("", 12, "bold"), relief="raised", bd=2, padx=15, pady=5)
        self.cancel_button = tk.Button(search_button_frame, text="キャンセル", command=self.controller.cancel_search, state="disabled", bg="#6c757d", fg="white", font=("", 12, "bold"), relief="raised", bd=2, padx=15, pady=5)
        self.show_search_button()
        
        progress_frame = ttk.Frame(right_frame)
        progress_frame.grid(row=2, column=0, sticky="ew", pady=5)
        self.progress = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.progress.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.progress_label = ttk.Label(progress_frame, text="待機中...", width=25, anchor="w")
        self.progress_label.pack(side=tk.LEFT, padx=5)

        notebook = ttk.Notebook(self.top_controls_frame)
        notebook.grid(row=1, column=0, padx=5, pady=5, sticky="ew")

        history_tab = ttk.Frame(notebook)
        notebook.add(history_tab, text='検索履歴')
        history_tab.columnconfigure(1, weight=1)
        
        ttk.Label(history_tab, text="履歴:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.history_combo = ttk.Combobox(history_tab, textvariable=self.history_var, state="readonly")
        self.history_combo.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.history_combo.bind("<<ComboboxSelected>>", self.controller.on_history_selected)
        ttk.Button(history_tab, text="削除", command=self.controller.delete_selected_history).grid(row=0, column=2, padx=5, pady=5, sticky="w")
        
        ttk.Label(history_tab, text="ソート:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.history_sort_combobox = ttk.Combobox(history_tab, textvariable=self.history_sort_var, state="readonly", width=10, values=("追加順", "ディレクトリ順", "キーワード順"))
        self.history_sort_combobox.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.history_sort_combobox.bind("<<ComboboxSelected>>", lambda e: self.controller.update_history_display())
        ttk.Button(history_tab, text="お気に入り設定保存", command=self.controller.save_favorite_settings).grid(row=1, column=2, padx=5, pady=5, sticky="w")
        
        file_op_tab = ttk.Frame(notebook)
        notebook.add(file_op_tab, text='ファイル操作')
        file_op_tab.columnconfigure(1, weight=1)
        
        ttk.Label(file_op_tab, text="保存先:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        dest_entry = DroppableEntry(file_op_tab, self.controller, textvariable=self.dest_path_var)
        dest_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.add_context_menu(dest_entry)
        ttk.Button(file_op_tab, text="選択", command=self.controller.browse_dest_directory).grid(row=0, column=2, padx=5, pady=5)
        
        ttk.Separator(file_op_tab, orient='horizontal').grid(row=1, column=0, columnspan=3, sticky='ew', pady=10)
        
        conversion_frame = ttk.Frame(file_op_tab)
        conversion_frame.grid(row=2, column=0, columnspan=3, sticky='ew', padx=5)
        conversion_frame.columnconfigure(0, weight=1)
        conversion_frame.columnconfigure(1, weight=1)
        
        folder_convert_btn = ttk.Button(conversion_frame, text="フォルダ内のPNGをWebPに変換...", command=self.controller.convert_folder_to_webp)
        folder_convert_btn.grid(row=0, column=0, sticky='ew', padx=(0, 2))
        Tooltip(folder_convert_btn, "指定したフォルダ内の全てのPNGファイルを、メタ情報を保持したままWebPに一括変換します。")
        
        selected_convert_btn = ttk.Button(conversion_frame, text="選択中のPNGをWebPに変換", command=self.controller.convert_selected_to_webp)
        selected_convert_btn.grid(row=0, column=1, sticky='ew', padx=(2, 0))
        Tooltip(selected_convert_btn, "検索結果で選択されているPNGファイルのみをWebPに変換します。")
        
        ttk.Separator(file_op_tab, orient='horizontal').grid(row=3, column=0, columnspan=3, sticky='ew', pady=5)
        zip_frame = ttk.Frame(file_op_tab)
        zip_frame.grid(row=4, column=0, columnspan=3, sticky='ew', padx=5)
        zip_convert_btn = ttk.Button(
            zip_frame, 
            text="ZIP内の画像をWebPに変換...",
            command=self.controller.convert_zip_to_webp
        )
        zip_convert_btn.pack(fill='x')
        Tooltip(zip_convert_btn, 
            "ZIPファイル内の画像を一括でWebP形式に変換し、新しいZIPファイルとして保存します。\n" +
            "メタデータの保持やリサイズなどの詳細オプションも設定できます。"
        )

        display_tab = ttk.Frame(notebook)
        notebook.add(display_tab, text='表示・ソート')
        
        sort_frame = ttk.Frame(display_tab)
        sort_frame.pack(side='left', padx=10, pady=5)
        ttk.Label(sort_frame, text="結果ソート:").pack(anchor='w')
        sort_combobox = ttk.Combobox(sort_frame, textvariable=self.sort_var, state="readonly", width=15, values=("ファイル名昇順", "ファイル名降順", "更新日時昇順", "更新日時降順", "解像度(昇順)", "解像度(降順)"))
        sort_combobox.pack(anchor='w')
        sort_combobox.bind("<<ComboboxSelected>>", self.controller.on_sort_changed)

        display_count_frame = ttk.Frame(display_tab)
        display_count_frame.pack(side='left', padx=10, pady=5)
        ttk.Label(display_count_frame, text="最大表示件数:").pack(anchor='w')
        max_display_entry = ttk.Entry(display_count_frame, textvariable=self.max_display_var, width=8)
        max_display_entry.pack(anchor='w')
        self.add_context_menu(max_display_entry)

        thumb_size_frame = ttk.Labelframe(display_tab, text="サムネイルサイズ")
        thumb_size_frame.pack(side='left', padx=10, pady=5, fill='x')
        
        thumb_size_slider = ttk.Scale(thumb_size_frame, from_=100, to=400, orient='horizontal', variable=self.thumb_size_var, length=120)
        thumb_size_slider.grid(row=0, column=0, sticky='ew')
        Tooltip(thumb_size_slider, "スライダーを動かしてサムネイルの表示サイズを変更します")

        size_entry = ttk.Entry(thumb_size_frame, textvariable=self.thumb_size_var, width=5)
        size_entry.grid(row=0, column=1, sticky='w', padx=(5,0))
        size_entry.bind("<Return>", lambda e: self._on_thumb_size_change())
        
        reset_button = ttk.Button(thumb_size_frame, text="リセット", width=8, command=self._reset_thumb_size)
        reset_button.grid(row=0, column=2, sticky='w', padx=5)
        default_size = AppConfig().thumbnail_display_size
        Tooltip(reset_button, f"デフォルトのサイズ ({default_size}px) に戻します")
        
        paging_frame = ttk.Frame(display_tab)
        paging_frame.pack(side='left', padx=10, pady=5)
        self.prev_page_btn = ttk.Button(paging_frame, text="◀ 前へ", command=self.prev_page)
        self.prev_page_btn.pack(fill='x', expand=True)
        self.next_page_btn = ttk.Button(paging_frame, text="次へ ▶", command=self.next_page)
        self.next_page_btn.pack(fill='x', expand=True)
        
        page_jump_frame = ttk.Frame(display_tab)
        page_jump_frame.pack(side='left', padx=10, pady=5)
        self.page_info_label = ttk.Label(page_jump_frame, text="ページ 1/1")
        self.page_info_label.pack(pady=2)
        page_jump_inner_frame = ttk.Frame(page_jump_frame)
        page_jump_inner_frame.pack()
        page_jump_entry = ttk.Entry(page_jump_inner_frame, textvariable=self.page_jump_var, width=5)
        page_jump_entry.pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(page_jump_inner_frame, text="移動", command=self.jump_to_page).pack(side=tk.LEFT)

        latest_files_tab = ttk.Frame(notebook)
        notebook.add(latest_files_tab, text='最新ファイル')
        ttk.Label(latest_files_tab, text="最新ファイル表示件数:").pack(side=tk.LEFT, padx=5, pady=5)
        latest_files_count_entry = ttk.Entry(latest_files_tab, textvariable=self.novel_ai_count_var, width=5)
        latest_files_count_entry.pack(side=tk.LEFT, padx=5, pady=5)
        self.add_context_menu(latest_files_count_entry)
        
        latest_files_btn = ttk.Button(latest_files_tab, text="最新更新ファイル表示", command=self.controller.show_latest_images)
        latest_files_btn.pack(side=tk.LEFT, padx=5, pady=5)
        Tooltip(latest_files_btn, "フォルダ内のファイルを更新日時順にスキャンし、最新のものを表示します。")
        
        self.setup_tooltips()
        self.setup_keyboard_navigation()

    def _schedule_thumb_resize(self, *args):
        if self._thumb_resize_job:
            self.root.after_cancel(self._thumb_resize_job)
        self._thumb_resize_job = self.root.after(250, self._on_thumb_size_change)

    def _on_thumb_size_change(self):
        self.thumbnails.clear()
        self.controller.on_sort_changed(refresh=False)
        
    def _reset_thumb_size(self):
        default_size = AppConfig().thumbnail_display_size
        self.thumb_size_var.set(default_size)

    def show_cancel_button(self):
        if not self.search_button or not self.cancel_button: return
        self.search_button.grid_forget()
        self.search_button.pack_forget()
        
        if self.button_manager_mode == 'grid':
            self.cancel_button.grid(row=0, column=0, sticky="ew")
        else:
            self.cancel_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.cancel_button.config(state="normal")

    def show_search_button(self):
        if not self.search_button or not self.cancel_button: return
        self.cancel_button.grid_forget()
        self.cancel_button.pack_forget()

        if self.button_manager_mode == 'grid':
            self.search_button.grid(row=0, column=0, sticky="ew")
        else:
            self.search_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

    def setup_tooltips(self):
        if self.search_button: Tooltip(self.search_button, "指定した条件で検索を開始します")
        if self.cancel_button: Tooltip(self.cancel_button, "実行中の検索を中止します")
        if self.history_combo: Tooltip(self.history_combo, "過去の検索履歴を表示・選択します")
        if self.history_sort_combobox: Tooltip(self.history_sort_combobox, "履歴の表示順を変更します")
        if self.prev_page_btn: Tooltip(self.prev_page_btn, "前のページに移動します")
        if self.next_page_btn: Tooltip(self.next_page_btn, "次のページに移動します")

    def setup_keyboard_navigation(self):
        if self.search_entry:
            self.root.bind_all('<Control-f>', lambda e: self.search_entry.focus_set())
            self.search_entry.focus_set()
        self.root.bind_all('<F1>', self.show_help)

    def show_help(self, event=None):
        help_text = """
画像メタ情報検索くん v5.5 - ヘルプ

【表示モード】
- シンプル: 基本的な検索機能のみを表示します。
- フル: 履歴やファイル操作など、全ての機能を表示します。
  右上のスイッチで切り替え可能です。

【基本的な使い方】
1. フォルダ選択エリアに検索したいフォルダをドラッグ＆ドロップするか、クリックして選択します。
2. 検索バーにキーワード（プロンプトの一部など）を入力し、Enterキーを押します。
3. 「検索スタート」ボタンでも検索を開始できます。

【ショートカットキー】
- Ctrl + F: 検索バーにフォーカスを移動します。
- F1: このヘルプを表示します。

【その他】
- サムネイルをCtrl+クリックすると、チェックボックスと同様に選択/選択解除ができます。
- 画像を右クリックすると、プロンプトのコピーなどの便利な機能が使えます。
- 検索結果の画像は、他のフォルダに直接ドラッグ＆ドロップしてファイル操作が可能です。
"""
        messagebox.showinfo("ヘルプ", help_text)

    def update_progress(self, value, text=""):
        if not self.progress: return
        self.progress["value"] = value
        if text:
            self.progress_label.config(text=text)
        elif 0 < value < 100:
            self.progress_label.config(text=f"検索中... {int(value)}%")
        elif value >= 100 and not text:
            self.progress_label.config(text="完了")
        elif value == 0 and not text:
            self.progress_label.config(text="待機中...")
    
    def get_search_parameters(self):
        return {
            "dir_path": self.dir_path_var.get(),
            "keyword": self.keyword_var.get(),
            "match_type": self.match_type_var.get(),
            "and_search": self.and_search_var.get(),
            "include_negative": self.include_negative_var.get(),
            "recursive_search": self.recursive_search_var.get(),
        }

    def get_selected_files(self):
        return [f for f, var in self.selected_files_vars.items() if var.get()]

    def show_context_menu(self, event, file_path):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="全ファイル選択", command=self.select_all_files)
        menu.add_command(label="全選択解除", command=self.deselect_all_files)
        menu.add_separator()
        menu.add_command(label="ファイル名コピー", command=lambda: self.controller.copy_to_clipboard(os.path.basename(file_path), "ファイル名"))
        menu.add_command(label="名前変更", command=lambda: self.controller.rename_file(file_path))
        menu.add_separator()
        menu.add_command(label="メタ情報表示", command=lambda: self.controller.show_metadata(file_path))
        menu.add_command(label="フォルダを開く", command=lambda: self.controller.open_folder(os.path.dirname(file_path)))
        menu.add_separator()
        prompt_menu = tk.Menu(menu, tearoff=0)
        prompt_menu.add_command(label="ベースプロンプトをコピー", command=lambda: self.controller.copy_base_caption(file_path))
        char_captions = self.controller.get_char_captions(file_path)
        if char_captions:
            char_sub_menu = tk.Menu(prompt_menu, tearoff=0)
            for idx, c_text in enumerate(char_captions):
                preview = c_text.strip()[:15] + ("…" if len(c_text.strip()) > 15 else "")
                char_sub_menu.add_command(label=f"キャラ {idx+1}: {preview}", command=lambda i=idx: self.controller.copy_char_caption(file_path, i))
            prompt_menu.add_cascade(label="キャラクタープロンプト", menu=char_sub_menu)
        menu.add_cascade(label="プロンプトコピー", menu=prompt_menu)
        negative_menu = tk.Menu(menu, tearoff=0)
        negative_menu.add_command(label="ベースネガティブをコピー", command=lambda: self.controller.copy_base_negative(file_path))
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
        if not self.history_combo: return
        current_value = self.history_var.get()
        display_list = [f"{item[0]} | {item[1]} | {item[2]}" if isinstance(item, (list, tuple)) and len(item) >= 3 else str(item) for item in sorted_history]
        self.history_combo["values"] = display_list
        if current_value in display_list:
            self.history_var.set(current_value)
        else:
            self.history_var.set("")

    def set_favorite_settings(self, settings):
        self.dir_path_var.set(settings.get("dir_path", ""))
        self.keyword_var.set(settings.get("keyword", ""))
        self.match_type_var.set(settings.get("match_type", "partial"))
        self.sort_var.set(settings.get("sort", "更新日時降順"))
        self.include_negative_var.set(settings.get("include_negative", False))
        self.and_search_var.set(settings.get("and_search", True))
        self.recursive_search_var.set(settings.get("recursive_search", True))
        self.max_display_var.set(settings.get("max_display", self.config.max_display_items))
        self.novel_ai_count_var.set(settings.get("novel_ai_count", 10))

    def on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _bind_mousewheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def on_resize_frame(self, event):
        if self._resize_after_id:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(300, lambda: self.controller.on_sort_changed(refresh=False))

    def add_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="切り取り", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="コピー", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="ペースト", command=lambda: widget.event_generate("<<Paste>>"))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.controller.on_sort_changed(refresh=False)

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.controller.on_sort_changed(refresh=False)

    def jump_to_page(self):
        try:
            page_num = int(self.page_jump_var.get())
            if 1 <= page_num <= self.total_pages:
                self.current_page = page_num - 1
                self.controller.on_sort_changed(refresh=False)
            else:
                messagebox.showerror("エラー", f"1から{self.total_pages}の間のページ番号を入力してください。")
        except ValueError:
            messagebox.showerror("エラー", "有効なページ番号（数値）を入力してください。")
        finally:
            self.page_jump_var.set("")

    def select_all_files(self):
        [var.set(True) for var in self.selected_files_vars.values()]

    def deselect_all_files(self):
        [var.set(False) for var in self.selected_files_vars.values()]

class ImageViewerWindow(tk.Toplevel):
    def __init__(self, parent, controller, file_list, start_index=0):
        super().__init__(parent)
        self.controller = controller
        self.file_list = file_list
        self.current_index = start_index
        self.current_zoom = 1.0
        self.pil_image = None
        self.resize_timer = None
        self.title("画像ビューア")
        self.geometry(self.controller.config.viewer_geometry)
        self.configure(bg="gray20")
        self.protocol("WM_DELETE_WINDOW", self.on_viewer_closing)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        btn_frame = ttk.Frame(self, style="TFrame")
        btn_frame.grid(row=0, column=0, sticky="ew", pady=5, padx=5)
        ttk.Button(btn_frame, text="← 前へ", command=self.prev_image).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="次へ →", command=self.next_image).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="拡大 (+)", command=lambda: self.zoom(1.2)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="縮小 (-)", command=lambda: self.zoom(0.8)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="フィット", command=self.fit_to_screen).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="原寸", command=self.original_size).pack(side=tk.LEFT, padx=5)
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
        self.bind_events()
        self.load_and_display_image()
        self.focus_set()
    def on_viewer_closing(self):
        self.controller.config.viewer_geometry = self.geometry()
        self.destroy()
    def bind_events(self):
        self.bind("<Left>", lambda e: self.prev_image())
        self.bind("<Right>", lambda e: self.next_image())
        self.bind("<Control-equal>", lambda e: self.zoom(1.2))
        self.bind("<Control-plus>", lambda e: self.zoom(1.2))
        self.bind("<Control-minus>", lambda e: self.zoom(0.8))
        self.bind("<Control-0>", lambda e: self.fit_to_screen())
        self.canvas.bind("<Button-3>", self.show_viewer_context_menu)
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.bind("<Configure>", self.on_window_resize)
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
            self.canvas.create_text(self.winfo_width()/2, self.winfo_height()/2, text=f"画像を開けませんでした\n{e}", fill="white", font=("", 16))
    def display_image(self):
        if not self.pil_image:
            return
        width, height = int(self.pil_image.width * self.current_zoom), int(self.pil_image.height * self.current_zoom)
        if width <= 0 or height <= 0:
            return
        resized_img = self.pil_image.resize((width, height), LANCZOS_RESAMPLING)
        self.tk_image = ImageTk.PhotoImage(resized_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        self.canvas.config(scrollregion=self.canvas.bbox("all"))
    def zoom(self, factor):
        self.current_zoom = max(0.01, min(self.current_zoom * factor, 10.0))
        self.display_image()
    def fit_to_screen(self, event=None):
        if not self.pil_image:
            return
        canvas_w, canvas_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if canvas_w < 50 or canvas_h < 50:
            self.after(50, self.fit_to_screen)
            return
        img_w, img_h = self.pil_image.size
        if img_w == 0 or img_h == 0:
            return
        zoom_w, zoom_h = (canvas_w - 10) / img_w, (canvas_h - 10) / img_h
        self.current_zoom = min(zoom_w, zoom_h, 1.0)
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
        self.controller.view.show_context_menu(event, file_path)
    def on_button_press(self, event):
        self.canvas.scan_mark(event.x, event.y)
    def on_move_press(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)
    def on_window_resize(self, event):
        if self.resize_timer:
            self.after_cancel(self.resize_timer)
        self.resize_timer = self.after(300, self.fit_to_screen)

class WebPConversionOptionsDialog(tk.Toplevel):
    """WebP変換オプション設定ダイアログ"""
    def __init__(self, parent):
        super().__init__(parent)
        self.transient(parent)
        self.title("WebP変換オプション")
        self.result = None

        self.lossless_var = tk.BooleanVar(value=True)
        self.quality_var = tk.IntVar(value=95)
        self.method_var = tk.IntVar(value=6)
        self.preserve_metadata_var = tk.BooleanVar(value=True)
        self.include_non_images_var = tk.BooleanVar(value=True)
        self.keep_failed_var = tk.BooleanVar(value=True)
        self.resize_var = tk.BooleanVar(value=False)
        self.max_size_var = tk.IntVar(value=1920)

        self.create_widgets()
        self.geometry(f"+{parent.winfo_x()+50}+{parent.winfo_y()+50}")
        self.grab_set()
        self.wait_window(self)

    def create_widgets(self):
        quality_frame = ttk.LabelFrame(self, text="画質設定", padding=10)
        quality_frame.pack(fill='x', padx=10, pady=5)
        
        lossless_cb = ttk.Checkbutton(quality_frame, text="ロスレス圧縮（最高品質）", variable=self.lossless_var, command=self.toggle_quality_settings)
        lossless_cb.pack(anchor='w')

        self.quality_controls_frame = ttk.Frame(quality_frame)
        self.quality_controls_frame.pack(fill='x', pady=5, padx=20)
        
        ttk.Label(self.quality_controls_frame, text="品質:").pack(side='left')
        self.quality_slider = ttk.Scale(self.quality_controls_frame, from_=1, to=100, variable=self.quality_var, orient='horizontal', length=200)
        self.quality_slider.pack(side='left', padx=5, expand=True, fill='x')
        self.quality_label = ttk.Label(self.quality_controls_frame, text="95", width=3)
        self.quality_label.pack(side='left')
        self.quality_var.trace('w', lambda *args: self.quality_label.config(text=str(self.quality_var.get())))

        options_frame = ttk.LabelFrame(self, text="変換オプション", padding=10)
        options_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Checkbutton(options_frame, text="メタデータを保持", variable=self.preserve_metadata_var).pack(anchor='w')
        ttk.Checkbutton(options_frame, text="画像以外のファイルも含める", variable=self.include_non_images_var).pack(anchor='w')
        ttk.Checkbutton(options_frame, text="変換失敗時は元ファイルを保持", variable=self.keep_failed_var).pack(anchor='w')
        
        resize_frame = ttk.Frame(options_frame)
        resize_frame.pack(fill='x', pady=(10, 0))
        
        resize_cb = ttk.Checkbutton(resize_frame, text="最大サイズを制限:", variable=self.resize_var, command=self.toggle_resize_settings)
        resize_cb.pack(side='left')
        
        self.size_entry = ttk.Entry(resize_frame, textvariable=self.max_size_var, width=6, state='disabled')
        self.size_entry.pack(side='left', padx=5)
        ttk.Label(resize_frame, text="px").pack(side='left')

        button_frame = ttk.Frame(self)
        button_frame.pack(fill='x', padx=10, pady=10)
        
        ttk.Button(button_frame, text="キャンセル", command=self.cancel_clicked).pack(side='right', padx=5)
        ttk.Button(button_frame, text="変換開始", command=self.ok_clicked).pack(side='right')

        self.toggle_quality_settings()
        self.toggle_resize_settings()

    def toggle_quality_settings(self):
        if self.lossless_var.get():
            self.quality_slider.config(state='disabled')
            self.quality_label.config(foreground='gray')
        else:
            self.quality_slider.config(state='normal')
            self.quality_label.config(foreground='black')

    def toggle_resize_settings(self):
        state = 'normal' if self.resize_var.get() else 'disabled'
        self.size_entry.config(state=state)

    def ok_clicked(self):
        self.result = {
            'lossless': self.lossless_var.get(),
            'quality': self.quality_var.get() if not self.lossless_var.get() else 100,
            'method': self.method_var.get(),
            'preserve_metadata': self.preserve_metadata_var.get(),
            'include_non_images': self.include_non_images_var.get(),
            'keep_failed_originals': self.keep_failed_var.get(),
            'max_size': self.max_size_var.get() if self.resize_var.get() else None
        }
        self.destroy()

    def cancel_clicked(self):
        self.result = None
        self.destroy()