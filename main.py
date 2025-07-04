import tkinter as tk
from tkinter import messagebox
import logging
import tkinterdnd2

from config import AppConfig
from model import ImageSearchModel
from view import ImageSearchView
from controller import ImageSearchController

def main():
    # ★★★ 変更点: デバッグレベルのロギングを有効化 ★★★
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] [%(name)s] - %(message)s'
    )
    
    config = AppConfig.load()
    model = None
    try:
        root = tkinterdnd2.Tk()
        
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
        if "tkinterdnd2" in str(e).lower():
            error_msg += "\n\n'pip install tkinterdnd2' を実行してください。"
        else:
            error_msg += "\n\n'pip install -r requirements.txt' を実行してください。"
        messagebox.showerror("ライブラリ不足", error_msg)
    except Exception as e:
        logging.critical("アプリケーションの起動中に致命的なエラーが発生しました。", exc_info=True)
        messagebox.showerror("致命的なエラー", f"アプリケーションの起動に失敗しました。\n\nエラー: {e}")
    finally:
        config.save()
        if model:
            model.close()
        logging.info("アプリケーションを終了しました")

if __name__ == "__main__":
    main()