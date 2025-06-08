import os
import shutil
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, simpledialog
import threading
import queue
import time
import json
import logging
import sys
import subprocess
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from view import ImageSearchView, ImageViewerWindow
from model import ImageSearchModel
from config import AppConfig

class NewFileHandler(FileSystemEventHandler):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def on_created(self, event):
        if event.is_directory: return
        self.controller.handle_new_file(event.src_path)

class ImageSearchController:
    def __init__(self, model: ImageSearchModel, view: ImageSearchView, config: AppConfig):
        self.model = model; self.view = view; self.config = config
        self.view.set_controller(self)
        self.queue = queue.Queue(); self.observer = None
        self.sorted_search_history = []
        self.current_matched_files = []
        self.current_matched_files_lock = threading.Lock()
        self.search_cancel_event = threading.Event()
        self.view.root.after(100, self.process_queue)

    def _get_all_files(self, directory, recursive):
        all_files = []
        try:
            if recursive:
                for root, _, files in os.walk(directory):
                    if self.search_cancel_event.is_set(): return None
                    for file in files:
                        if os.path.splitext(file)[1].lower() in self.config.supported_formats:
                            all_files.append(os.path.join(root, file))
            else:
                with os.scandir(directory) as it:
                    for entry in it:
                        if self.search_cancel_event.is_set(): return None
                        if entry.is_file() and os.path.splitext(entry.name)[1].lower() in self.config.supported_formats:
                            all_files.append(entry.path)
            return all_files
        except OSError as e:
            logging.error(f"ディレクトリへのアクセスエラー: {directory} -> {e}")
            self.queue.put({"type": "error", "message": f"フォルダにアクセスできません: {e}"})
            return None

    def _search_thread(self, params):
        self.search_cancel_event.clear()
        self.queue.put({"type": "search_started"})
        
        all_files = self._get_all_files(params["dir_path"], params["recursive_search"])
        if all_files is None: 
            if not self.search_cancel_event.is_set():
                self.queue.put({"type": "search_finished"})
            return

        total_files = len(all_files)
        if total_files == 0:
            self.queue.put({"type": "done", "params": params})
            return

        if total_files > self.config.large_search_warning_threshold:
            self.queue.put({"type": "confirm_large_search", "files": all_files, "params": params})
            return

        self._execute_search_tasks(all_files, params)

    def _execute_search_tasks(self, all_files, params):
        total_files = len(all_files)
        
        with ThreadPoolExecutor(max_workers=self.config.thread_pool_size) as executor:
            future_to_file = {executor.submit(self.model.get_metadata_and_thumbnail, f): f for f in all_files}
            
            for i, future in enumerate(as_completed(future_to_file)):
                if self.search_cancel_event.is_set():
                    for f in future_to_file: f.cancel()
                    self.queue.put({"type": "search_cancelled"})
                    return
                
                try:
                    metadata, _, file_path = future.result()
                    if self.match_keyword(params["keyword"], params["match_type"], params["and_search"], metadata):
                        self.queue.put({"type": "result_found", "file_path": file_path})
                except Exception as e:
                    logging.error(f"ファイル処理中に例外発生: {future_to_file[future]}", exc_info=True)
                
                self.queue.put({"type": "progress", "value": ((i + 1) / total_files) * 100})
        
        self.queue.put({"type": "done", "params": params})

    def start_search(self, event=None):
        params = self.view.get_search_parameters()
        if not params["dir_path"] or not os.path.isdir(params["dir_path"]): messagebox.showerror("エラー", "検索対象フォルダを指定してください。"); return
        if not params["keyword"]: messagebox.showerror("エラー", "検索キーワードを入力してください。"); return
        
        with self.current_matched_files_lock:
            self.current_matched_files.clear()
        self.view.current_page = 0
        self.view.layout_results([], refresh=True)
        self.view.update_progress(0, "ファイルリスト作成中...")
        self.start_directory_watch(params["dir_path"])
        
        threading.Thread(target=self._search_thread, args=(params,), daemon=True).start()
        
    def process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                msg_type = msg.get("type")
                
                if msg_type == "search_started":
                    self.view.search_button.pack_forget(); self.view.cancel_button.pack(side=tk.LEFT, padx=5); self.view.cancel_button.config(state="normal")
                
                elif msg_type == "progress": self.view.update_progress(msg["value"])
                
                elif msg_type == "result_found":
                    with self.current_matched_files_lock:
                        self.current_matched_files.append(msg["file_path"])
                    if len(self.current_matched_files) <= self.config.max_display_items:
                        self.on_sort_changed(refresh=False)
                    else:
                        # ページネーション情報のみ更新
                        total_items = len(self.current_matched_files)
                        max_items = self.view.max_display_var.get() or 1
                        self.view.total_pages = (total_items + max_items - 1) // max_items
                        self.view.page_info_label.config(text=f"ページ {self.view.current_page + 1}/{self.view.total_pages} ({total_items}件)")

                elif msg_type == "done" or msg_type == "search_cancelled" or msg_type == "search_finished":
                    self.view.cancel_button.pack_forget(); self.view.search_button.pack(side=tk.LEFT, padx=5)
                    if msg_type == "done":
                        params = msg.get("params")
                        if params:
                            cache_key = (params["dir_path"], params["match_type"], params["keyword"], params["include_negative"], params["and_search"], params["recursive_search"])
                            self.model.add_history(cache_key); self.update_history_display()
                        self.on_sort_changed()
                        self.view.update_progress(100, text=f"{len(self.current_matched_files)} 件見つかりました")
                    elif msg_type == "search_cancelled":
                        self.view.update_progress(0, text="検索がキャンセルされました")

                elif msg_type == "new_file_matched":
                    with self.current_matched_files_lock:
                        if msg["file_path"] not in self.current_matched_files:
                            self.current_matched_files.append(msg["file_path"])
                    self.on_sort_changed(refresh=False)

                elif msg_type == "error": messagebox.showerror("エラー", msg["message"])
                
                elif msg_type == "confirm_large_search":
                    if messagebox.askokcancel("大規模検索の警告", f"{len(msg['files'])}件のファイルを検索します。\n処理に時間がかかる可能性があります。続行しますか？"):
                         threading.Thread(target=self._execute_search_tasks, args=(msg['files'], msg['params']), daemon=True).start()
                    else:
                        self.queue.put({"type": "search_cancelled"})

        except queue.Empty: pass
        finally: self.view.root.after(100, self.process_queue)
    
    def on_sort_changed(self, event=None, refresh=True):
        with self.current_matched_files_lock:
            sorted_files = self.model.apply_sort(list(self.current_matched_files), self.view.sort_var.get())
        
        if self.config.enable_predictive_caching:
            self._trigger_predictive_caching(sorted_files)
        
        files_with_thumbs = [(path, self.model._get_from_db(path).get('thumbnail') if self.model._get_from_db(path) else None) for path in sorted_files]
        self.view.layout_results(files_with_thumbs, refresh=refresh)
    
    def _trigger_predictive_caching(self, sorted_files):
        start_index = (self.view.current_page + 1) * self.config.max_display_items
        end_index = start_index + (self.config.predictive_pages * self.config.max_display_items)
        files_to_preload = sorted_files[start_index:end_index]

        if files_to_preload:
            threading.Thread(target=self._predictive_cache_task, args=(files_to_preload,), daemon=True).start()

    def _predictive_cache_task(self, file_paths):
        for file_path in file_paths:
            _, cached_thumb, _ = self.model.get_metadata_and_thumbnail(file_path)
            if cached_thumb: continue
            future = self.view.thumbnail_executor.submit(self.view._create_and_get_webp, file_path, None)
            future.add_done_callback(lambda f, p=file_path: self._on_predictive_cached(f, p))
    
    def _on_predictive_cached(self, future, file_path):
        try:
            _, webp_bytes = future.result()
            if webp_bytes: self.model.cache_thumbnail(file_path, webp_bytes)
        except Exception: pass

    def cancel_search(self):
        self.search_cancel_event.set()
        self.view.cancel_button.config(state="disabled")
        self.view.update_progress(self.view.progress['value'], "キャンセル中...")
        
    def on_closing(self):
        self.cancel_search(); self.view.shutdown_executors()
        if self.view.root.winfo_exists(): self.config.window_geometry = self.view.root.geometry()
        self.config.save()
        if self.observer: self.observer.stop(); self.observer.join()
        self.model.close(); self.view.root.destroy()
        
    def match_keyword(self, keyword, match_type, is_and, text):
        if not text: return False
        tokens = keyword.split();
        if not tokens: return True
        text_lower = text.lower()
        if match_type == "exact": return keyword.lower() == text_lower
        if is_and: return all(token.lower() in text_lower for token in tokens)
        else: return any(token.lower() in text_lower for token in tokens)
    
    def update_history_display(self):
        history = self.model.load_history(); sort_option = self.view.history_sort_var.get()
        indexed_history = list(enumerate(history))
        def get_sort_key(item, index, default=""): return str(item[index]).lower() if isinstance(item, (list, tuple)) and len(item) > index else default
        try:
            if sort_option == "ディレクトリ順": indexed_history.sort(key=lambda x: get_sort_key(x[1], 0))
            elif sort_option == "キーワード順": indexed_history.sort(key=lambda x: get_sort_key(x[1], 2))
        except IndexError: logging.warning("履歴のソート中にエラーが発生しました。")
        self.sorted_search_history = [item for _, item in indexed_history]
        self.view.update_history_display(self.sorted_search_history)
        
    def on_history_selected(self, event):
        idx = self.view.history_combo.current();
        if idx < 0: return
        item = self.sorted_search_history[idx]
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            settings = { "dir_path": item[0], "match_type": item[1], "keyword": item[2], "include_negative": item[3] if len(item) > 3 else False, "and_search": item[4] if len(item) > 4 else True, "recursive_search": item[5] if len(item) > 5 else True }
            self.view.dir_path_var.set(settings["dir_path"]); self.view.match_type_var.set(settings["match_type"]); self.view.keyword_var.set(settings["keyword"])
            self.view.include_negative_var.set(settings["include_negative"]); self.view.and_search_var.set(settings["and_search"]); self.view.recursive_search_var.set(settings["recursive_search"])
            self.start_search()

    def delete_selected_history(self):
        idx = self.view.history_combo.current()
        if idx < 0: messagebox.showinfo("情報", "削除する履歴が選択されていません。"); return
        item_to_delete = self.sorted_search_history[idx]
        if self.model.delete_history_item(item_to_delete):
            self.update_history_display(); self.view.history_var.set(""); messagebox.showinfo("情報", "選択した履歴を削除しました。")
        else: messagebox.showerror("エラー", "履歴の削除に失敗しました。")

    def browse_directory(self):
        dir_path = filedialog.askdirectory(initialdir=self.view.dir_path_var.get());
        if dir_path: self.view.dir_path_var.set(dir_path); self.start_directory_watch(dir_path)

    def browse_dest_directory(self):
        dir_path = filedialog.askdirectory(initialdir=self.view.dest_path_var.get());
        if dir_path: self.view.dest_path_var.set(dir_path)
    
    def copy_selected_files(self):
        dest = self.view.dest_path_var.get()
        if not dest: messagebox.showerror("保存先が未指定", "ファイルをコピーするには、まず「保存先フォルダ」を選択してください。"); return
        if not os.path.isdir(dest): messagebox.showerror("フォルダが見つかりません", f"指定されたフォルダ「{dest}」が存在しません。"); return
        if self._file_operation(shutil.copy2, "コピー") and messagebox.askyesno("完了", f"コピーが完了しました。\n保存先フォルダ「{os.path.basename(dest)}」を開きますか？"): self.open_folder(dest)

    def move_selected_files(self):
        if self._file_operation(shutil.move, "移動"): self.start_search()

    def _file_operation(self, func, op_name):
        selected_list = self.view.get_selected_files()
        if not selected_list: messagebox.showinfo("情報", f"{op_name}対象が選択されていません。"); return False
        dest = self.view.dest_path_var.get(); errors = []
        for file_path in selected_list:
            try:
                if os.path.exists(file_path): func(file_path, dest)
                else: errors.append(f"{os.path.basename(file_path)}: 見つかりません")
            except Exception as e: errors.append(f"{os.path.basename(file_path)}: {e}")
        if errors: messagebox.showerror("エラー", f"{op_name}中にエラーが発生しました:\n" + "\n".join(errors)); return False
        return True

    def copy_to_clipboard(self, text, data_type):
        try: self.view.root.clipboard_clear(); self.view.root.clipboard_append(text); messagebox.showinfo("情報", f"{data_type}をクリップボードにコピーしました。")
        except tk.TclError as e: messagebox.showerror("エラー", f"クリップボードへのコピーに失敗しました: {e}")

    def open_folder(self, folder_path):
        if not os.path.isdir(folder_path): messagebox.showerror("エラー", "指定されたパスは有効なフォルダではありません。"); return
        try:
            if sys.platform == 'win32': subprocess.Popen(['explorer', os.path.normpath(folder_path)])
            elif sys.platform == 'darwin': subprocess.Popen(['open', folder_path])
            else: subprocess.Popen(['xdg-open', folder_path])
        except Exception as e: messagebox.showerror("エラー", f"フォルダを開けませんでした: {e}")

    def get_char_captions(self, file_path):
        meta_text = self.model.get_raw_metadata(file_path); json_str = self.model.extract_json_block(meta_text, '"v4_prompt"')
        if json_str:
            try: return [c.get("char_caption", "") for c in json.loads(json_str).get("caption", {}).get("char_captions", [])]
            except json.JSONDecodeError: pass
        return []
        
    def get_char_negatives(self, file_path):
        meta_text = self.model.get_raw_metadata(file_path); json_str = self.model.extract_json_block(meta_text, '"v4_negative_prompt"')
        if json_str:
            try: return [c.get("char_caption", "") for c in json.loads(json_str).get("caption", {}).get("char_captions", [])]
            except json.JSONDecodeError: pass
        return []

    def copy_base_caption(self, file_path):
        meta_text = self.model.get_raw_metadata(file_path); json_str = self.model.extract_json_block(meta_text, '"v4_prompt"')
        if not json_str: messagebox.showerror("エラー", "ベースプロンプトの抽出に失敗しました。"); return
        try:
            caption = json.loads(json_str).get("caption", {}).get("base_caption", "")
            if caption: self.copy_to_clipboard(caption, "ベースプロンプト")
            else: messagebox.showerror("エラー", "ベースプロンプトが見つかりません。")
        except json.JSONDecodeError as e: messagebox.showerror("エラー", f"JSONパースエラー: {e}")
            
    def copy_char_caption(self, file_path, index):
        captions = self.get_char_captions(file_path)
        if index < len(captions): self.copy_to_clipboard(captions[index], f"キャラクタープロンプト {index+1}")
        else: messagebox.showerror("エラー", "指定のキャラクタープロンプトが見つかりませんでした。")

    def copy_base_negative(self, file_path):
        meta_text = self.model.get_raw_metadata(file_path); json_str = self.model.extract_json_block(meta_text, '"v4_negative_prompt"')
        if not json_str: messagebox.showerror("エラー", "ベースネガティブの抽出に失敗しました。"); return
        try:
            negative = json.loads(json_str).get("caption", {}).get("base_caption", "")
            if negative: self.copy_to_clipboard(negative, "ベースネガティブ")
            else: messagebox.showerror("エラー", "ベースネガティブが見つかりません。")
        except json.JSONDecodeError as e: messagebox.showerror("エラー", f"JSONパースエラー: {e}")
            
    def copy_char_negative(self, file_path, index):
        negatives = self.get_char_negatives(file_path)
        if index < len(negatives): self.copy_to_clipboard(negatives[index], f"キャラクターネガティブ {index+1}")
        else: messagebox.showerror("エラー", "指定のキャラクターネガティブが見つかりませんでした。")

    def show_full_image(self, file_path):
        with self.current_matched_files_lock: sorted_files = self.model.apply_sort(list(self.current_matched_files), self.view.sort_var.get())
        try: ImageViewerWindow(self.view.root, self, sorted_files, sorted_files.index(file_path))
        except ValueError: messagebox.showerror("エラー", "ファイルがリストに見つかりません。")
            
    def show_metadata(self, file_path):
        metadata_text = self.model.get_raw_metadata(file_path)
        meta_win = tk.Toplevel(self.view.root); meta_win.title(f"メタ情報: {os.path.basename(file_path)}"); meta_win.geometry("600x500")
        text_frame = ttk.Frame(meta_win); text_frame.pack(fill="both", expand=True, padx=10, pady=10); text_frame.rowconfigure(0, weight=1); text_frame.columnconfigure(0, weight=1)
        scrollbar = ttk.Scrollbar(text_frame); scrollbar.grid(row=0, column=1, sticky="ns")
        text_widget = tk.Text(text_frame, wrap="word", yscrollcommand=scrollbar.set, relief="sunken", bd=1); text_widget.grid(row=0, column=0, sticky="nsew"); scrollbar.config(command=text_widget.yview)
        display_text = metadata_text if metadata_text else "メタ情報が見つかりませんでした。"
        text_widget.config(state="normal"); text_widget.delete("1.0", tk.END); text_widget.insert("1.0", display_text); text_widget.config(state="disabled")
        button_frame = ttk.Frame(meta_win); button_frame.pack(pady=5)
        copy_all_btn = ttk.Button(button_frame, text="全文コピー", command=lambda: self.copy_to_clipboard(display_text, "メタ情報")); copy_all_btn.pack(side=tk.LEFT, padx=5)
        meta_win.focus_set()

    def rename_file(self, file_path):
        if not os.path.exists(file_path): messagebox.showerror("エラー", "ファイルが見つかりません。リストを更新します。"); self.start_search(); return
        current_name = os.path.basename(file_path)
        new_name = simpledialog.askstring("名前変更", "新しいファイル名:", initialvalue=current_name)
        if new_name and new_name != current_name:
            new_path = os.path.join(os.path.dirname(file_path), new_name)
            try: os.rename(file_path, new_path); messagebox.showinfo("完了", "ファイル名を変更しました。"); self.start_search()
            except OSError as e: messagebox.showerror("エラー", f"名前変更失敗: {e}")

    def load_favorite_settings(self):
        settings = self.model.load_favorite_settings()
        if settings: self.view.set_favorite_settings(settings);
        if self.view.dir_path_var.get(): self.start_directory_watch(self.view.dir_path_var.get())

    def save_favorite_settings(self):
        settings = { "dir_path": self.view.dir_path_var.get(), "keyword": self.view.keyword_var.get(), "match_type": self.view.match_type_var.get(), "sort": self.view.sort_var.get(), "include_negative": self.view.include_negative_var.get(), "and_search": self.view.and_search_var.get(), "recursive_search": self.view.recursive_search_var.get(), "max_display": self.view.max_display_var.get(), "novel_ai_count": self.view.novel_ai_count_var.get() }
        if self.model.save_favorite_settings(settings): messagebox.showinfo("情報", "お気に入り設定を保存しました。")
        else: messagebox.showerror("エラー", "お気に入り設定の保存に失敗しました。")

    def load_image_prompt(self):
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", self.config.supported_formats)])
        if not file_path: return
        meta_text = self.model.get_raw_metadata(file_path)
        prompt_match = re.search(r'^(.*?)\nNegative prompt: ', meta_text, re.DOTALL)
        if prompt_match: prompt = prompt_match.group(1).strip(); self.view.keyword_var.set(prompt); messagebox.showinfo("情報", "抽出したプロンプトを検索キーワードに設定しました。")
        else: self.view.keyword_var.set(meta_text); messagebox.showwarning("警告", "複雑なプロンプトは見つかりませんでした。メタ情報全体をキーワードに設定します。")
    
    def show_novel_ai_images(self):
        directory = self.view.dir_path_var.get()
        if not directory or not os.path.isdir(directory): messagebox.showerror("エラー", "有効なフォルダを指定してください。"); return
        count = self.view.novel_ai_count_var.get()
        if count <= 0: messagebox.showerror("エラー", "表示件数は1以上にしてください。"); return
        self.view.update_progress(0, text="NovelAI画像を探しています...")
        threading.Thread(target=self._show_novelai_thread, args=(directory, count), daemon=True).start()

    def _show_novelai_thread(self, directory, count):
        all_files = self._get_all_files(directory, True)
        if all_files:
            with ThreadPoolExecutor(max_workers=self.config.thread_pool_size) as executor:
                # 戻り値は不要なので、単純に実行
                list(executor.map(lambda f: self.model.get_metadata_and_thumbnail(f), all_files))
        
        novel_ai_files = self.model.get_novelai_files_from_db(directory, count)
        if not novel_ai_files:
            self.queue.put({"type": "error", "message": "指定フォルダ内にNovelAI画像が見つかりませんでした。"})
            self.queue.put({"type": "search_finished"}) # ボタンを元に戻す
            return
        
        with self.current_matched_files_lock:
            self.current_matched_files = novel_ai_files
        self.queue.put({"type": "done"})

    def start_directory_watch(self, directory):
        if self.observer: self.observer.stop(); self.observer.join()
        if not os.path.isdir(directory): return
        event_handler = NewFileHandler(self)
        self.observer = Observer(); self.observer.schedule(event_handler, directory, recursive=self.view.recursive_search_var.get()); self.observer.start()

    def handle_new_file(self, file_path):
        if os.path.splitext(file_path)[1].lower() not in self.config.supported_formats: return
        for _ in range(5):
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                try:
                    with open(file_path, 'rb') as f: f.read(1024)
                except OSError: time.sleep(0.1); continue
                else: break
            else: time.sleep(0.1)
        else:
            logging.warning(f"ファイルにアクセスできなかったためスキップ: {file_path}"); return
        
        params = self.view.get_search_parameters()
        metadata, _, _ = self.model.get_metadata_and_thumbnail(file_path)
        if self.match_keyword(params["keyword"], params["match_type"], params["and_search"], metadata):
            self.queue.put({"type": "new_file_matched", "file_path": file_path})
    
    def cache_thumbnail(self, file_path, webp_bytes):
        self.model.cache_thumbnail(file_path, webp_bytes)