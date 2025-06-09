import tkinter as tk
from tkinter import messagebox
import logging
from config import AppConfig
from model import ImageSearchModel
from view import ImageSearchView
from controller import ImageSearchController

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    config = AppConfig.load()
    model = None
    try:
        root = tk.Tk()
        root.geometry(config.window_geometry)
        model = ImageSearchModel(config)
        view = ImageSearchView(root, config)
        controller = ImageSearchController(model, view, config)
        view.create_widgets()
        controller.load_favorite_settings()
        controller.update_history_display()
        logging.info("アプリケーションを起動しました")
        root.mainloop()
    except ImportError as e:
        error_msg = f"必要なライブラリがインストールされていません: {e}"
        logging.critical(error_msg)
        messagebox.showerror("ライブラリ不足", f"{error_msg}\n\n'pip install -r requirements.txt' を実行してください。")
    except Exception as e:
        logging.critical("アプリケーションの起動中に致命的なエラーが発生しました。", exc_info=True)
        messagebox.showerror("致命的なエラー", f"アプリケーションの起動に失敗しました。\n\nエラー: {e}")
    finally:
        # 以下のウィンドウ情報保存処理は、controllerのon_closingで行われるため不要。
        # 実行タイミングの問題でエラーの原因となるため削除。
        # if 'view' in locals() and view.root.winfo_exists():
        #      config.window_geometry = view.root.geometry()
        config.save()
        if model:
            model.close()
        logging.info("アプリケーションを終了しました")

if __name__ == "__main__":
    main()