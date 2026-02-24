import enum

from abc import ABC, abstractmethod
from typing import Optional, Union

from app.enmus.note_enums import DownloadQuality
from app.models.notes_model import AudioDownloadResult
from app.models.transcriber_model import SubtitleFetchResult
from os import getenv
QUALITY_MAP = {
    "fast": "32",
    "medium": "64",
    "slow": "128"
}


class Downloader(ABC):
    def __init__(self):
        #TODO 需要修改为可配置
        self.quality = QUALITY_MAP.get('fast')
        self.cache_data=getenv('DATA_DIR')

    @abstractmethod
    def download(self, video_url: str, output_dir: str = None,
                 quality: DownloadQuality = "fast", need_video: Optional[bool] = False) -> AudioDownloadResult:
        '''

        :param need_video:
        :param video_url: 资源链接
        :param output_dir: 输出路径 默认根目录data
        :param quality: 音频质量 fast | medium | slow
        :return:返回一个 AudioDownloadResult 类
        '''
        pass

    def get_media_info(self, video_url: str, output_dir: str = None) -> Optional[AudioDownloadResult]:
        """
        仅提取媒体元信息（标题、时长、封面、video_id 等），不下载音频文件。
        默认返回 None，由调用方决定是否降级为下载音频。
        """
        return None

    @staticmethod
    def download_video(self, video_url: str,
                       output_dir: Union[str, None] = None) -> str:
        pass

    def download_subtitles(self, video_url: str, output_dir: str = None,
                           langs: list = None) -> SubtitleFetchResult:
        '''
        尝试获取平台字幕（人工字幕或自动生成字幕）

        :param video_url: 视频链接
        :param output_dir: 输出路径
        :param langs: 优先语言列表，如 ['zh-Hans', 'zh', 'en']
        :return: 字幕获取结果（包含字幕、状态与诊断信息）
        '''
        return SubtitleFetchResult(
            outcome="unavailable",
            reason_code="NOT_IMPLEMENTED",
            message="当前平台未实现字幕抓取",
        )
