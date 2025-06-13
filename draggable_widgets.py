import tkinter as tk
from tkinter import ttk, messagebox
import tkinterdnd2
from tkinterdnd2 import DND_FILES, COPY
import os
import sys
import logging

class DragGhostWindow:
    def __init__(self, parent, thumbnail=None, file_count=1, file_name="", opacity=0.8):
        self.toplevel = parent.winfo_toplevel()
        self.window = tk.Toplevel(self.toplevel)
        self.window.overrideredirect(True)
        self.window.attributes('-alpha', opacity)
        self.window.attributes('-topmost', True)
        main_frame = tk.Frame(self.window, bg='#CCCCCC', relief='flat')
        main_frame.pack(padx=1, pady=1)
        inner_frame = tk.Frame(main_frame, bg='white', padx=3, pady=3)
        inner_frame.pack(fill='both', expand=True)
        
        if thumbnail:
            try:
                img_label = tk.Label(inner_frame, image=thumbnail, bg='white', bd=0)
                img_label.image = thumbnail
                img_label.pack(padx=2, pady=2)
            except Exception as e:
                logging.error(f"ゴーストウィンドウのサムネイル表示エラー: {e}", exc_info=True)
                self._show_text_fallback(inner_frame, file_name, file_count)
        else:
            self._show_text_fallback(inner_frame, file_name, file_count)
            
        if file_count > 1:
            count_frame = tk.Frame(main_frame, bg='#FFE4B5', relief='ridge', bd=1)
            count_frame.pack(fill='x', padx=2, pady=(0, 2))
            count_label = tk.Label(
                count_frame, text=f"🗎 {file_count} 個のファイル",
                font=('Arial', 9, 'bold'), bg='#FFE4B5', fg='#333333'
            )
            count_label.pack(pady=1)
            
        self.window.geometry('+9999+9999')
        self.window.update_idletasks()
        
    def _show_text_fallback(self, frame, file_name, file_count):
        text = os.path.basename(file_name) if file_count == 1 else f"{file_count} 個のファイル"
        icon = "🖼️" if file_count == 1 else "📁"
        content_frame = tk.Frame(frame, bg='white')
        content_frame.pack(padx=15, pady=10)
        icon_label = tk.Label(content_frame, text=icon, font=('Arial', 16), bg='white')
        icon_label.pack()
        text_label = tk.Label(
            content_frame, text=text[:30] + "..." if len(text) > 30 else text,
            bg='white', fg='black', font=('Arial', 9)
        )
        text_label.pack()
        
    def move(self, x, y):
        if self.window and self.window.winfo_exists():
            self.window.geometry(f'+{x+15}+{y+15}')
    def destroy(self):
        if self.window and self.window.winfo_exists():
            self.window.destroy()
            self.window = None

# ★★★★★ エラー修正：tkinterdnd2の標準的なイベント処理方法に全体を書き換え ★★★★★
class DraggableImageLabel(ttk.Label):
    def __init__(self, parent, controller, file_path, **kwargs):
        super().__init__(parent, **kwargs)
        self.controller = controller
        self.file_path = file_path
        self.current_photo_image = None
        self.ghost_window = None
        
        # ドラッグソースとしてウィジェットを登録
        self.drag_source_register(DND_FILES)
        
        # ドラッグの開始と終了を検知するイベントに、ゴーストウィンドウの管理を紐付け
        self.dnd_bind('<<DragInitCmd>>', self.on_drag_init)
        self.dnd_bind('<<DragMoveCmd>>', self.on_drag_move)
        self.dnd_bind('<<DragEndCmd>>', self.on_drag_end)

        # 通常のクリックイベント
        self.bind("<Control-Button-1>", self.on_ctrl_click)

    def on_drag_init(self, event):
        """ドラッグ開始時に呼び出される。ゴーストウィンドウを作成し、ドラッグするデータを準備する。"""
        # ゴーストウィンドウを作成
        self.create_ghost_window(event)
        
        # ドラッグするファイルリストを準備
        selected_files = self.controller.view.get_selected_files()
        files_to_drag = selected_files if self.file_path in selected_files else [self.file_path]
        data = ' '.join([f'{{{os.path.abspath(p)}}}' for p in files_to_drag])
        
        # ライブラリにドラッグ操作の情報を返す
        return (COPY, DND_FILES, data)

    def on_drag_move(self, event):
        """ドラッグ中に呼び出される。ゴーストウィンドウをマウスに追従させる。"""
        if self.ghost_window:
            self.ghost_window.move(event.x_root, event.y_root)

    def on_drag_end(self, event):
        """ドラッグ終了時に呼び出される。ゴーストウィンドウを破棄する。"""
        self.cleanup_ghost_window()

    def create_ghost_window(self, event):
        """ゴーストウィンドウを作成する。"""
        if self.ghost_window or not self.controller.config.enable_drag_ghost:
            return
            
        selected_files = self.controller.view.get_selected_files()
        file_count = len(selected_files) if self.file_path in selected_files else 1
        
        self.ghost_window = DragGhostWindow(
            self,
            thumbnail=self.current_photo_image,
            file_count=file_count,
            file_name=self.file_path,
            opacity=self.controller.config.drag_ghost_opacity
        )
        if self.ghost_window:
            # 最初の位置を即座に更新
            self.ghost_window.move(event.x_root, event.y_root)

    def cleanup_ghost_window(self):
        """ゴーストウィンドウをクリーンアップする。"""
        if self.ghost_window:
            self.ghost_window.destroy()
            self.ghost_window = None

    def on_ctrl_click(self, event):
        """Ctrl+クリックで選択状態を反転"""
        var = self.controller.view.selected_files_vars.get(self.file_path)
        if var:
            var.set(not var.get())
        return "break"

# === 以下は変更なしのクラス群 ===
class DroppableEntry(ttk.Entry):
    def __init__(self, parent, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.controller = controller
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)
        self.dnd_bind('<<DragEnter>>', self.on_drag_enter)
        self.dnd_bind('<<DragLeave>>', self.on_drag_leave)
        self.normal_bg = self.cget('background')
    def on_drag_enter(self, event):
        self.configure(background='lightblue')
        return event.action
    def on_drag_leave(self, event):
        self.configure(background=self.normal_bg)
    def on_drop(self, event):
        self.configure(background=self.normal_bg)
        paths = self.winfo_toplevel().tk.splitlist(event.data)
        first_item = paths[0]
        if os.path.isdir(first_item):
            self.controller.view.dest_path_var.set(first_item)
        elif os.path.isfile(first_item):
            dest_folder = self.get()
            if os.path.isdir(dest_folder):
                self.controller.handle_drop_to_folder(paths, dest_folder)
            else:
                messagebox.showerror("エラー", "有効な保存先フォルダが指定されていません。")
        return event.action

class DropActionDialog(tk.Toplevel):
    def __init__(self, parent, message):
        super().__init__(parent)
        self.transient(parent)
        self.title("アクションの選択")
        self.result = "cancel"
        ttk.Label(self, text=message, justify=tk.LEFT, wraplength=350).pack(padx=20, pady=10)
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="コピー", command=lambda: self.set_action("copy")).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="移動", command=lambda: self.set_action("move")).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="キャンセル", command=lambda: self.set_action("cancel")).pack(side=tk.LEFT, padx=10)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.geometry(f"+{parent.winfo_x()+50}+{parent.winfo_y()+50}")
        self.wait_window(self)
    def set_action(self, action):
        self.result = action
        self.destroy()
    def cancel(self):
        self.result = "cancel"
        self.destroy()

class ProgressDialog(tk.Toplevel):
    def __init__(self, parent, title, max_value):
        super().__init__(parent)
        self.transient(parent)
        self.title(title)
        self.progress_var = tk.DoubleVar()
        self.progressbar = ttk.Progressbar(self, variable=self.progress_var, maximum=max_value, length=300)
        self.progressbar.pack(padx=20, pady=10)
        self.label_var = tk.StringVar()
        self.label = ttk.Label(self, textvariable=self.label_var)
        self.label.pack(pady=(0, 10))
        self.max_value = max_value
        self.update(0)
        self.geometry(f"+{parent.winfo_x()+100}+{parent.winfo_y()+100}")
        self.update_idletasks()
    def update(self, current_value):
        self.progress_var.set(current_value)
        self.label_var.set(f"{current_value} / {self.max_value}")
        self.update_idletasks()
    def close(self):
        self.destroy()