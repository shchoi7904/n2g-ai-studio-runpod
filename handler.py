"""
Runpod Serverless Handler for Video Rendering with FFmpeg
GPU-accelerated video encoding using NVENC
Supports Google Drive download and upload
"""

import runpod
import subprocess
import base64
import os
import tempfile
import json
import urllib.request
import ssl
import math
from pathlib import Path

# Google Drive API
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False
    print("[Handler] Google API not available - upload to Drive disabled")

def download_base64_file(base64_data: str, filepath: str) -> None:
    """Base64 데이터를 파일로 저장"""
    if ',' in base64_data:
        base64_data = base64_data.split(',')[1]

    with open(filepath, 'wb') as f:
        f.write(base64.b64decode(base64_data))

def download_from_url(url: str, filepath: str) -> bool:
    """URL에서 파일 다운로드 (Google Drive 지원)"""
    try:
        # SSL 검증 비활성화 (Google Drive 다운로드용)
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Google Drive direct download URL 처리
        if 'drive.google.com' in url or url.startswith('gdrive:'):
            # gdrive:FILE_ID 형식 처리
            if url.startswith('gdrive:'):
                file_id = url.replace('gdrive:', '')
            else:
                file_id = url

            # Google Drive direct download URL
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        else:
            download_url = url

        print(f"[Handler] Downloading from: {download_url[:80]}...")

        req = urllib.request.Request(download_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        with urllib.request.urlopen(req, context=ssl_context, timeout=120) as response:
            with open(filepath, 'wb') as f:
                f.write(response.read())

        file_size = os.path.getsize(filepath)
        print(f"[Handler] Downloaded: {filepath} ({file_size} bytes)")
        return file_size > 100

    except Exception as e:
        print(f"[Handler] Download failed: {e}")
        return False

def get_or_create_folder(service, folder_name: str, parent_id: str) -> str:
    """폴더를 찾거나 없으면 생성"""
    # 기존 폴더 검색
    query = f"name='{folder_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, fields='files(id, name)').execute()
    files = results.get('files', [])

    if files:
        print(f"[Handler] Found existing folder: {folder_name} ({files[0]['id']})")
        return files[0]['id']

    # 폴더 생성
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    folder = service.files().create(body=folder_metadata, fields='id').execute()
    print(f"[Handler] Created folder: {folder_name} ({folder['id']})")
    return folder['id']

def get_folder_by_path(service, base_folder_id: str, path_parts: list) -> str:
    """경로를 따라 폴더를 찾거나 생성 (예: ['채널명', '영상1', 'video'])"""
    current_folder_id = base_folder_id

    for part in path_parts:
        if part:  # 빈 문자열 무시
            current_folder_id = get_or_create_folder(service, part, current_folder_id)

    return current_folder_id

def upload_to_google_drive(filepath: str, folder_id: str, credentials_json: str, folder_path: list = None) -> dict:
    """Google Drive에 파일 업로드

    Args:
        filepath: 업로드할 파일 경로
        folder_id: 기본 폴더 ID (루트)
        credentials_json: 서비스 계정 JSON
        folder_path: 하위 폴더 경로 리스트 (예: ['채널명', '영상1', 'video'])
    """
    if not GOOGLE_API_AVAILABLE:
        return {'error': 'Google API not available'}

    try:
        # 서비스 계정 인증
        creds_dict = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']
        )

        service = build('drive', 'v3', credentials=credentials)

        # 폴더 경로가 지정된 경우 해당 경로의 폴더 찾기/생성
        target_folder_id = folder_id
        if folder_path and len(folder_path) > 0:
            print(f"[Handler] Finding/creating folder path: {'/'.join(folder_path)}")
            target_folder_id = get_folder_by_path(service, folder_id, folder_path)

        # 파일 메타데이터
        file_name = os.path.basename(filepath)
        file_metadata = {
            'name': file_name,
            'parents': [target_folder_id] if target_folder_id else []
        }

        # 업로드
        media = MediaFileUpload(filepath, mimetype='video/mp4', resumable=True)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink, webContentLink'
        ).execute()

        print(f"[Handler] Uploaded to Drive: {file.get('id')} in folder {target_folder_id}")

        return {
            'fileId': file.get('id'),
            'webViewLink': file.get('webViewLink'),
            'webContentLink': file.get('webContentLink'),
            'folderId': target_folder_id,
        }

    except Exception as e:
        print(f"[Handler] Drive upload failed: {e}")
        return {'error': str(e)}

def encode_file_to_base64(filepath: str) -> str:
    """파일을 base64로 인코딩"""
    with open(filepath, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')

def get_video_duration(filepath: str) -> float:
    """ffprobe로 비디오 길이 확인"""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            filepath
        ], capture_output=True, text=True)
        return float(result.stdout.strip())
    except:
        return 0

def create_subtitle_file(scenes: list, filepath: str, gap_duration: float = 0, last_scene_buffer: float = 1.0) -> None:
    """SRT 자막 파일 생성"""
    srt_content = ""
    start_time = 0
    subtitle_index = 1

    for i, scene in enumerate(scenes):
        is_last = i == len(scenes) - 1
        effective_duration = scene['duration'] + (last_scene_buffer if is_last else 0)

        if scene.get('subtitle'):
            end_time = start_time + scene['duration']

            def format_time(seconds):
                hrs = int(seconds // 3600)
                mins = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                ms = int((seconds % 1) * 1000)
                return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

            srt_content += f"{subtitle_index}\n"
            srt_content += f"{format_time(start_time)} --> {format_time(end_time)}\n"
            srt_content += f"{scene['subtitle']}\n\n"
            subtitle_index += 1

        start_time += effective_duration
        if not is_last and gap_duration > 0:
            start_time += gap_duration

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(srt_content)

def render_video(job_input: dict) -> dict:
    """메인 렌더링 함수"""
    scenes = job_input.get('scenes', [])
    show_subtitle = job_input.get('showSubtitle', False)
    output_format = job_input.get('outputFormat', 'mp4')
    resolution = job_input.get('resolution', '1080p')
    scene_gap_duration = job_input.get('sceneGapDuration', 0)
    subtitle_style = job_input.get('subtitleStyle', {})
    bgm = job_input.get('bgm')

    # Google Drive 업로드 설정 (환경변수에서 읽기)
    upload_to_drive = job_input.get('uploadToDrive', True)
    drive_folder_id = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '')
    drive_credentials = os.environ.get('GOOGLE_DRIVE_CREDENTIALS', '')
    drive_folder_path = job_input.get('driveFolderPath', [])  # ['채널명', '영상1', 'video']

    if not scenes:
        return {'error': '씬 데이터가 필요합니다.'}

    # 해상도 설정
    res_map = {
        '1440p': (2560, 1440),
        '1080p': (1920, 1080),
        '720p': (1280, 720),
        '480p': (854, 480),
    }
    width, height = res_map.get(resolution, (1920, 1080))
    fps = 30

    print(f"[Handler] 렌더링 시작: {len(scenes)}개 씬, 해상도: {resolution} ({width}x{height})")
    print(f"[Handler] Drive 업로드: {upload_to_drive}, 폴더: {drive_folder_id[:20] if drive_folder_id else 'N/A'}...")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        segment_files = []
        audio_files = []
        actual_durations = []

        # 1. 씬별 미디어 파일 저장 및 세그먼트 생성
        for i, scene in enumerate(scenes):
            is_last = i == len(scenes) - 1
            gap_to_include = 0 if is_last else scene_gap_duration
            end_buffer = 1.0 if is_last else 0  # 마지막 씬에 1초 버퍼 추가 (오디오 잘림 방지)

            print(f"[Handler] 씬 {i+1}/{len(scenes)} 처리 중...")

            # 미디어 파일 저장 (URL 또는 base64)
            media_file = None
            is_video = False

            # 비디오 처리
            if scene.get('videoData'):
                media_file = temp_path / f"video_{i}.mp4"
                download_base64_file(scene['videoData'], str(media_file))
                is_video = True
            elif scene.get('videoUrl'):
                media_file = temp_path / f"video_{i}.mp4"
                if download_from_url(scene['videoUrl'], str(media_file)):
                    is_video = True
                else:
                    media_file = None

            # 이미지 처리 (비디오가 없는 경우)
            if not media_file:
                if scene.get('imageData'):
                    media_file = temp_path / f"image_{i}.png"
                    download_base64_file(scene['imageData'], str(media_file))
                    is_video = False
                elif scene.get('imageUrl'):
                    image_url = scene['imageUrl']
                    ext = 'jpg'
                    media_file = temp_path / f"image_{i}.{ext}"
                    if not download_from_url(image_url, str(media_file)):
                        return {'error': f'씬 {i+1} 이미지 다운로드 실패'}
                    is_video = False
                else:
                    return {'error': f'씬 {i+1}에 미디어 데이터가 없습니다.'}

            # 오디오 저장 및 실제 길이 측정
            audio_duration = scene.get('duration', 3)
            audio_file = None

            if scene.get('audioData'):
                audio_file = temp_path / f"audio_{i}.mp3"
                download_base64_file(scene['audioData'], str(audio_file))
            elif scene.get('audioUrl'):
                audio_file = temp_path / f"audio_{i}.mp3"
                if not download_from_url(scene['audioUrl'], str(audio_file)):
                    audio_file = None

            if audio_file and audio_file.exists():
                audio_files.append(str(audio_file))
                measured = get_video_duration(str(audio_file))
                if measured > 0:
                    audio_duration = measured

            target_duration = audio_duration + gap_to_include + end_buffer
            # 프레임 수 계산: ceiling 사용하여 오디오가 잘리지 않도록
            exact_frames = math.ceil(target_duration * fps) + 1

            segment_file = temp_path / f"segment_{i}.mp4"

            # GPU 가속 인코딩 (NVENC)
            if is_video:
                cmd = [
                    'ffmpeg', '-y',
                    '-stream_loop', '-1',
                    '-i', str(media_file),
                    '-vf', f'fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black',
                    '-frames:v', str(exact_frames),
                    '-c:v', 'h264_nvenc',
                    '-preset', 'p4',
                    '-rc', 'vbr',
                    '-cq', '23',
                    '-pix_fmt', 'yuv420p',
                    '-r', str(fps),
                    '-an',
                    str(segment_file)
                ]
            else:
                cmd = [
                    'ffmpeg', '-y',
                    '-framerate', str(fps),
                    '-loop', '1',
                    '-i', str(media_file),
                    '-frames:v', str(exact_frames),
                    '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black',
                    '-c:v', 'h264_nvenc',
                    '-preset', 'p4',
                    '-rc', 'vbr',
                    '-cq', '23',
                    '-pix_fmt', 'yuv420p',
                    '-r', str(fps),
                    str(segment_file)
                ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"[Handler] NVENC 실패, CPU 폴백 사용")
                if is_video:
                    cmd = [
                        'ffmpeg', '-y',
                        '-stream_loop', '-1',
                        '-i', str(media_file),
                        '-vf', f'fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black',
                        '-frames:v', str(exact_frames),
                        '-c:v', 'libx264',
                        '-preset', 'ultrafast',
                        '-crf', '23',
                        '-pix_fmt', 'yuv420p',
                        '-r', str(fps),
                        '-an',
                        str(segment_file)
                    ]
                else:
                    cmd = [
                        'ffmpeg', '-y',
                        '-framerate', str(fps),
                        '-loop', '1',
                        '-i', str(media_file),
                        '-frames:v', str(exact_frames),
                        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black',
                        '-c:v', 'libx264',
                        '-preset', 'ultrafast',
                        '-crf', '23',
                        '-pix_fmt', 'yuv420p',
                        '-r', str(fps),
                        str(segment_file)
                    ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    return {'error': f'세그먼트 {i+1} 렌더링 실패: {result.stderr[:500]}'}

            segment_files.append(str(segment_file))
            actual_durations.append({
                'sceneKey': scene.get('sceneKey', f'scene_{i}'),
                'duration': audio_duration
            })

        print(f"[Handler] 모든 세그먼트 생성 완료: {len(segment_files)}개")

        # 2. 세그먼트 병합
        concat_file = temp_path / 'concat.txt'
        with open(concat_file, 'w') as f:
            for seg in segment_files:
                f.write(f"file '{seg}'\n")

        merged_video = temp_path / f'merged.{output_format}'
        subprocess.run([
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
            str(merged_video)
        ], capture_output=True)

        # 3. 오디오 병합
        merged_audio = None
        if audio_files:
            audio_concat = temp_path / 'audio_concat.txt'
            with open(audio_concat, 'w') as f:
                for j, af in enumerate(audio_files):
                    f.write(f"file '{af}'\n")
                    if j < len(audio_files) - 1 and scene_gap_duration > 0:
                        silence = temp_path / f'silence_{j}.mp3'
                        subprocess.run([
                            'ffmpeg', '-y',
                            '-f', 'lavfi',
                            '-i', f'anullsrc=r=44100:cl=stereo',
                            '-t', str(scene_gap_duration),
                            '-c:a', 'libmp3lame',
                            '-b:a', '128k',
                            str(silence)
                        ], capture_output=True)
                        f.write(f"file '{silence}'\n")

            merged_audio = temp_path / 'merged_audio.mp3'
            subprocess.run([
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(audio_concat),
                '-c', 'copy',
                str(merged_audio)
            ], capture_output=True)

        # 4. BGM 믹싱
        if bgm and merged_audio:
            bgm_file = temp_path / 'bgm.mp3'
            bgm_loaded = False

            if bgm.get('data'):
                download_base64_file(bgm['data'], str(bgm_file))
                bgm_loaded = True
            elif bgm.get('url'):
                bgm_loaded = download_from_url(bgm['url'], str(bgm_file))

            if bgm_loaded and bgm_file.exists():
                total_duration = get_video_duration(str(merged_video))
                bgm_volume = bgm.get('volume', 30) / 100
                fade_in = bgm.get('fadeIn', 2)
                fade_out = bgm.get('fadeOut', 3)

                mixed_audio = temp_path / 'mixed_audio.mp3'
                fade_out_start = max(0, total_duration - fade_out)

                filter_complex = f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{total_duration},volume={bgm_volume}"
                if fade_in > 0:
                    filter_complex += f",afade=t=in:st=0:d={fade_in}"
                if fade_out > 0:
                    filter_complex += f",afade=t=out:st={fade_out_start}:d={fade_out}"
                filter_complex += "[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[out]"

                subprocess.run([
                    'ffmpeg', '-y',
                    '-i', str(merged_audio),
                    '-i', str(bgm_file),
                    '-filter_complex', filter_complex,
                    '-map', '[out]',
                    '-c:a', 'libmp3lame',
                    '-b:a', '192k',
                    str(mixed_audio)
                ], capture_output=True)

                if mixed_audio.exists():
                    merged_audio = mixed_audio

        # 5. 자막 생성
        subtitle_file = None
        if show_subtitle:
            scenes_with_subtitles = [s for s in scenes if s.get('subtitle')]
            if scenes_with_subtitles:
                subtitle_file = temp_path / 'subtitles.srt'
                create_subtitle_file(scenes, str(subtitle_file), scene_gap_duration, 1.0)

        # 6. 최종 영상 생성
        output_file = temp_path / f'output.{output_format}'

        cmd = ['ffmpeg', '-y', '-i', str(merged_video)]
        if merged_audio:
            cmd.extend(['-i', str(merged_audio)])

        if subtitle_file and subtitle_file.exists():
            style_parts = [
                f"FontName={subtitle_style.get('fontName', 'Arial')}",
                f"FontSize={subtitle_style.get('fontSize', 24)}",
                f"PrimaryColour=&HFFFFFF",
                f"OutlineColour=&H000000",
                f"Bold={1 if subtitle_style.get('bold') else 0}",
                f"Outline={subtitle_style.get('outline', 2)}",
                f"Shadow={subtitle_style.get('shadow', 1)}",
                f"MarginV={subtitle_style.get('marginV', 50)}",
                f"Alignment={subtitle_style.get('alignment', 2)}",
            ]
            style_str = ','.join(style_parts)

            cmd.extend([
                '-vf', f"subtitles='{subtitle_file}':force_style='{style_str}'",
                '-c:v', 'h264_nvenc',
                '-preset', 'p4',
                '-rc', 'vbr',
                '-cq', '23'
            ])
        else:
            cmd.extend(['-c:v', 'copy'])

        if merged_audio:
            cmd.extend(['-c:a', 'aac', '-b:a', '128k'])

        cmd.append(str(output_file))

        result = subprocess.run(cmd, capture_output=True, text=True)

        # NVENC 실패 시 CPU 폴백
        if result.returncode != 0 and 'nvenc' in ' '.join(cmd).lower():
            cmd = [c.replace('h264_nvenc', 'libx264').replace('p4', 'ultrafast') for c in cmd]
            cmd = [c for c in cmd if c not in ['-rc', 'vbr', '-cq']]
            new_cmd = []
            skip_next = False
            for c in cmd:
                if skip_next:
                    skip_next = False
                    continue
                if c == '-cq':
                    skip_next = True
                    continue
                if c == '-rc':
                    skip_next = True
                    continue
                new_cmd.append(c)
            if '-c:v' in new_cmd:
                idx = new_cmd.index('-c:v')
                new_cmd.insert(idx + 2, '-crf')
                new_cmd.insert(idx + 3, '23')
            result = subprocess.run(new_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return {'error': f'최종 렌더링 실패: {result.stderr[:500]}'}

        # 7. 결과 처리
        if not output_file.exists():
            return {'error': '출력 파일이 생성되지 않았습니다.'}

        file_size = output_file.stat().st_size
        duration = get_video_duration(str(output_file))

        print(f"[Handler] 렌더링 완료: {duration}초, {round(file_size / (1024 * 1024), 2)}MB")

        # Google Drive 업로드
        if upload_to_drive and drive_folder_id and drive_credentials:
            print(f"[Handler] Google Drive 업로드 시작... 폴더 경로: {drive_folder_path}")
            upload_result = upload_to_google_drive(
                str(output_file),
                drive_folder_id,
                drive_credentials,
                drive_folder_path
            )

            if 'error' in upload_result:
                print(f"[Handler] Drive 업로드 실패: {upload_result['error']}")
                # 업로드 실패 시 base64 폴백
            else:
                print(f"[Handler] Drive 업로드 성공: {upload_result['fileId']}")
                return {
                    'success': True,
                    'uploadedToDrive': True,
                    'driveFileId': upload_result['fileId'],
                    'driveViewLink': upload_result.get('webViewLink'),
                    'driveDownloadLink': upload_result.get('webContentLink'),
                    'duration': duration,
                    'sizeMB': round(file_size / (1024 * 1024), 2),
                    'actualSegmentDurations': actual_durations,
                    'actualGapDuration': scene_gap_duration
                }

        # base64로 인코딩하여 반환 (파일이 작은 경우만)
        if file_size < 100 * 1024 * 1024:  # 100MB 미만만 base64
            video_base64 = encode_file_to_base64(str(output_file))
            return {
                'success': True,
                'videoData': f'data:video/{output_format};base64,{video_base64}',
                'duration': duration,
                'sizeMB': round(file_size / (1024 * 1024), 2),
                'actualSegmentDurations': actual_durations,
                'actualGapDuration': scene_gap_duration
            }
        else:
            return {
                'success': True,
                'error': f'파일이 너무 큽니다 ({round(file_size / (1024 * 1024), 2)}MB). Google Drive 업로드를 활성화하세요.',
                'duration': duration,
                'sizeMB': round(file_size / (1024 * 1024), 2),
            }

def handler(job):
    """Runpod 핸들러 진입점"""
    job_input = job.get('input', {})

    try:
        result = render_video(job_input)
        return result
    except Exception as e:
        import traceback
        return {'error': str(e), 'traceback': traceback.format_exc()}

runpod.serverless.start({'handler': handler})
