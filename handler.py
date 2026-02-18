"""
Runpod Serverless Handler for Video Rendering with FFmpeg
GPU-accelerated video encoding using NVENC
"""

import runpod
import subprocess
import base64
import os
import tempfile
import json
from pathlib import Path

def download_base64_file(base64_data: str, filepath: str) -> None:
    """Base64 데이터를 파일로 저장"""
    if ',' in base64_data:
        base64_data = base64_data.split(',')[1]

    with open(filepath, 'wb') as f:
        f.write(base64.b64decode(base64_data))

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

def create_subtitle_file(scenes: list, filepath: str, gap_duration: float = 0, last_scene_buffer: float = 0.3) -> None:
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

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        segment_files = []
        audio_files = []
        actual_durations = []

        # 1. 씬별 미디어 파일 저장 및 세그먼트 생성
        for i, scene in enumerate(scenes):
            is_last = i == len(scenes) - 1
            gap_to_include = 0 if is_last else scene_gap_duration
            end_buffer = 0.3 if is_last else 0

            # 미디어 파일 저장
            if scene.get('videoData'):
                media_file = temp_path / f"video_{i}.mp4"
                download_base64_file(scene['videoData'], str(media_file))
                is_video = True
            elif scene.get('imageData'):
                media_file = temp_path / f"image_{i}.png"
                download_base64_file(scene['imageData'], str(media_file))
                is_video = False
            else:
                return {'error': f'씬 {i+1}에 미디어 데이터가 없습니다.'}

            # 오디오 저장 및 실제 길이 측정
            audio_duration = scene.get('duration', 3)
            if scene.get('audioData'):
                audio_file = temp_path / f"audio_{i}.mp3"
                download_base64_file(scene['audioData'], str(audio_file))
                audio_files.append(str(audio_file))

                measured = get_video_duration(str(audio_file))
                if measured > 0:
                    audio_duration = measured

            target_duration = audio_duration + gap_to_include + end_buffer
            exact_frames = int(target_duration * fps) + 1

            segment_file = temp_path / f"segment_{i}.mp4"

            # GPU 가속 인코딩 (NVENC)
            if is_video:
                cmd = [
                    'ffmpeg', '-y',
                    '-stream_loop', '-1',
                    '-i', str(media_file),
                    '-vf', f'fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black',
                    '-frames:v', str(exact_frames),
                    '-c:v', 'h264_nvenc',  # GPU 인코딩
                    '-preset', 'p4',        # 빠른 프리셋
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
                # NVENC 실패 시 CPU 폴백
                cmd = [c.replace('h264_nvenc', 'libx264').replace('-preset', '-preset').replace('p4', 'ultrafast').replace('-rc', '-crf').replace('vbr', '').replace('-cq', '') for c in cmd if c and c != 'vbr']
                cmd = [c for c in cmd if c]  # 빈 문자열 제거
                # CPU 폴백 명령 재구성
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
                        # 무음 구간 생성
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

        # 4. BGM 믹싱 (있는 경우)
        if bgm and bgm.get('data') and merged_audio:
            bgm_file = temp_path / 'bgm.mp3'
            download_base64_file(bgm['data'], str(bgm_file))

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

        # 5. 자막 생성 (필요한 경우)
        subtitle_file = None
        if show_subtitle:
            scenes_with_subtitles = [s for s in scenes if s.get('subtitle')]
            if scenes_with_subtitles:
                subtitle_file = temp_path / 'subtitles.srt'
                create_subtitle_file(scenes, str(subtitle_file), scene_gap_duration, 0.3)

        # 6. 최종 영상 생성
        output_file = temp_path / f'output.{output_format}'

        cmd = ['ffmpeg', '-y', '-i', str(merged_video)]
        if merged_audio:
            cmd.extend(['-i', str(merged_audio)])

        if subtitle_file and subtitle_file.exists():
            # 자막 스타일 구성
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

            # GPU 디코딩 + CPU 자막 렌더링 + GPU 인코딩
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
            # -cq 값 제거
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
            # -crf 추가
            if '-c:v' in new_cmd:
                idx = new_cmd.index('-c:v')
                new_cmd.insert(idx + 2, '-crf')
                new_cmd.insert(idx + 3, '23')
            result = subprocess.run(new_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return {'error': f'최종 렌더링 실패: {result.stderr[:500]}'}

        # 7. 결과 반환
        if not output_file.exists():
            return {'error': '출력 파일이 생성되지 않았습니다.'}

        file_size = output_file.stat().st_size
        duration = get_video_duration(str(output_file))

        # base64로 인코딩하여 반환
        video_base64 = encode_file_to_base64(str(output_file))

        return {
            'success': True,
            'videoData': f'data:video/{output_format};base64,{video_base64}',
            'duration': duration,
            'sizeMB': round(file_size / (1024 * 1024), 2),
            'actualSegmentDurations': actual_durations,
            'actualGapDuration': scene_gap_duration
        }

def handler(job):
    """Runpod 핸들러 진입점"""
    job_input = job.get('input', {})

    try:
        result = render_video(job_input)
        return result
    except Exception as e:
        return {'error': str(e)}

runpod.serverless.start({'handler': handler})
