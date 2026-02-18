# Runpod GPU Video Rendering Handler

AI Studio용 GPU 가속 비디오 렌더링 서버리스 핸들러

## 배포 방법

### 방법 1: GitHub 연동 (권장)

1. 이 `runpod-handler` 폴더를 새 GitHub 리포지토리로 푸시
2. Runpod에서 "Connect GitHub" 클릭
3. 리포지토리 선택 후 배포

### 방법 2: Docker Hub 사용

```bash
# 1. Docker 이미지 빌드
cd runpod-handler
docker build -t your-dockerhub-username/ai-studio-render:latest .

# 2. Docker Hub에 푸시
docker push your-dockerhub-username/ai-studio-render:latest

# 3. Runpod에서 "Import from Docker Registry" 선택
# 이미지: your-dockerhub-username/ai-studio-render:latest
```

## Runpod 설정

### GPU 선택
- 권장: RTX 4090, RTX 3090, A100
- 최소: RTX 3080 (10GB VRAM)

### 환경 변수
필요 없음 (FFmpeg만 사용)

### 타임아웃
- Active Workers: 0 (자동 스케일링)
- Max Workers: 3
- Idle Timeout: 5초
- Execution Timeout: 300초 (5분)

## API 사용법

### 엔드포인트
```
POST https://api.runpod.ai/v2/{ENDPOINT_ID}/run
```

### 헤더
```
Authorization: Bearer {RUNPOD_API_KEY}
Content-Type: application/json
```

### 요청 본문
```json
{
  "input": {
    "scenes": [
      {
        "sceneKey": "scene_1",
        "imageData": "data:image/png;base64,...",
        "audioData": "data:audio/mp3;base64,...",
        "subtitle": "자막 텍스트",
        "duration": 5.5
      }
    ],
    "showSubtitle": true,
    "outputFormat": "mp4",
    "resolution": "1080p",
    "sceneGapDuration": 0.5,
    "subtitleStyle": {
      "fontName": "Arial",
      "fontSize": 24,
      "bold": false,
      "outline": 2,
      "shadow": 1,
      "marginV": 50,
      "alignment": 2
    },
    "bgm": {
      "data": "data:audio/mp3;base64,...",
      "volume": 30,
      "fadeIn": 2,
      "fadeOut": 3
    }
  }
}
```

### 응답
```json
{
  "id": "job_id",
  "status": "COMPLETED",
  "output": {
    "success": true,
    "videoData": "data:video/mp4;base64,...",
    "duration": 120.5,
    "sizeMB": 45.2,
    "actualSegmentDurations": [
      {"sceneKey": "scene_1", "duration": 5.5}
    ],
    "actualGapDuration": 0.5
  }
}
```

## 비용 예상

- RTX 4090: ~$0.44/시간
- 10분 영상 렌더링: ~1-2분 = ~$0.01-0.02
- 1시간 영상 렌더링: ~10-15분 = ~$0.07-0.10
