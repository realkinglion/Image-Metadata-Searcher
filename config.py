import json
import os
import logging
import shutil
from dataclasses import dataclass, asdict, field
from typing import Tuple

@dataclass
class AppConfig:
    """アプリケーション設定を管理するデータクラス"""
    # UI設定
    thumbnail_size: Tuple[int, int] = (200, 200)
    max_display_items: int = 50
    window_geometry: str = "1600x900"
    viewer_geometry: str = "1200x800"
    
    # パフォーマンス設定
    max_thumbnails_memory: int = 200
    thread_pool_size: int = os.cpu_count() or 4
    
    # キャッシュ設定
    memory_cache_size: int = 2000
    
    # ファイル設定
    supported_formats: Tuple[str, ...] = field(default_factory=lambda: ('.jpg', '.jpeg', '.png', '.tiff', '.webp'))
    config_file: str = "app_config.json"
    
    @classmethod
    def load(cls) -> 'AppConfig':
        config_path = cls().config_file
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                known_keys = {f.name for f in cls.__dataclass_fields__.values()}
                filtered_data = {k: v for k, v in data.items() if k in known_keys}
                if 'thumbnail_size' in filtered_data:
                    filtered_data['thumbnail_size'] = tuple(filtered_data['thumbnail_size'])
                if 'supported_formats' in filtered_data:
                    filtered_data['supported_formats'] = tuple(filtered_data['supported_formats'])
                return cls(**filtered_data)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logging.warning(f"設定ファイルの読み込みに失敗: {e}。デフォルト設定を使用します。")
        return cls()
    
    def save(self):
        temp_file = self.config_file + ".tmp"
        try:
            data_to_save = asdict(self)
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
            shutil.move(temp_file, self.config_file)
        except Exception as e:
            logging.error(f"設定の保存に失敗しました: {e}")
            if os.path.exists(temp_file):
                os.remove(temp_file)