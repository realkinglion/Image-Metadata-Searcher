import json
import os
import logging
import shutil
from dataclasses import dataclass, asdict, field
from typing import Tuple

@dataclass
class AppConfig:
    # 表示とキャッシュ設定
    thumbnail_cache_size: Tuple[int, int] = (400, 400)
    thumbnail_display_size: int = 150
    max_display_items: int = 50
    window_geometry: str = "1600x900"
    viewer_geometry: str = "1200x800"
    max_thumbnails_memory: int = 200
    
    # パフォーマンス設定
    thread_pool_size: int = os.cpu_count() or 4
    display_batch_size: int = 20
    large_search_warning_threshold: int = 20000
    
    # キャッシュ設定
    enable_predictive_caching: bool = True
    predictive_pages: int = 1
    memory_cache_size: int = 2000
    enable_thumbnail_caching: bool = True
    
    # 対応フォーマット
    supported_formats: Tuple[str, ...] = field(default_factory=lambda: ('.jpg', '.jpeg', '.png', '.tiff', '.webp'))
    
    # ドラッグ＆ドロップ設定
    enable_drag_ghost: bool = True
    drag_ghost_opacity: float = 0.8
    drag_threshold_pixels: int = 5
    
    # UI設定
    last_ui_mode: str = 'simple'
    
    # ★★★ ここからがサジェスト機能の新しい設定 ★★★
    enable_suggestions: bool = True
    suggestion_delay_ms: int = 300
    suggestion_min_chars: int = 2
    suggestion_max_results: int = 10
    suggestion_db_limit: int = 50
    suggestion_history_cache_ttl_sec: int = 5
    
    # 設定ファイル名
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
                    filtered_data['thumbnail_cache_size'] = tuple(filtered_data.pop('thumbnail_size'))
                
                if 'thumbnail_cache_size' in filtered_data:
                    filtered_data['thumbnail_cache_size'] = tuple(filtered_data['thumbnail_cache_size'])

                if 'supported_formats' in filtered_data:
                    filtered_data['supported_formats'] = tuple(filtered_data['supported_formats'])
                return cls(**filtered_data)
            except Exception as e:
                logging.warning(f"設定ファイルの読み込みに失敗: {e}。デフォルト設定を使用します。")
        return cls()
    
    def save(self):
        temp_file = self.config_file + ".tmp"
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=4)
            shutil.move(temp_file, self.config_file)
        except Exception as e:
            logging.error(f"設定の保存に失敗しました: {e}")
            if os.path.exists(temp_file):
                os.remove(temp_file)