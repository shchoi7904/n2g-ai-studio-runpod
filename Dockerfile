# Runpod Serverless - FFmpeg GPU Video Rendering
FROM runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04

# FFmpeg 설치 (NVENC 지원)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# 핸들러 복사
COPY handler.py /handler.py

# Runpod 핸들러 실행
CMD ["python", "-u", "/handler.py"]
