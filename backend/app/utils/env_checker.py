def is_cuda_available() -> bool:
    # 优先用 ctranslate2 自身的 CUDA 检测
    # ctranslate2 是 faster-whisper 的实际推理后端，其检测结果最准确
    # 且不依赖 torch 是否以 CUDA 版本安装
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return True
    except Exception:
        pass
    # 回退到 torch 检测
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False

def is_torch_installed() -> bool:
    try:
        import torch
        return True
    except ImportError:
        return False
