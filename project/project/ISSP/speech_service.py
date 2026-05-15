import os
import time
import threading
import ffmpeg
import azure.cognitiveservices.speech as speechsdk
import config

# Set to 3600 (1 hour) to support long recordings
TRANSCRIPTION_TIMEOUT = 3600


def _to_wav(input_path: str) -> str:
    """Convert any audio/video file to 16 kHz mono WAV for Azure Speech."""
    print(f"Converting to WAV: {input_path}")
    wav_path = input_path.rsplit('.', 1)[0] + '_converted.wav'

    try:
        (
            ffmpeg
            .input(input_path)
            .output(wav_path, acodec='pcm_s16le', ac=1, ar='16000')
            .overwrite_output()
            .run(quiet=True, capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        # Clean up any partial output file
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass
        error_msg = e.stderr.decode() if e.stderr else "Unknown FFmpeg error"
        print(f"FFmpeg Error: {error_msg}")
        raise RuntimeError(f"FFmpeg conversion failed: {error_msg}")

    return wav_path


def get_transcript_from_file(file_path: str) -> str:
    """
    Transcribe a file using Azure Speech-to-Text (continuous recognition).

    Raises:
        TimeoutError: If transcription exceeds TRANSCRIPTION_TIMEOUT seconds.
        RuntimeError: If Azure cancels due to an API/network error, or FFmpeg fails.
    """
    
    wav_path = _to_wav(file_path)
    tmp_created = True  # we always create a converted file now

    speech_recognizer = None

    # Shared state between callback threads and the main polling loop
    done_event = threading.Event()
    transcript_parts = []
    recognition_error: list[Exception] = []  # list used as mutable container

    def recognized_cb(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            transcript_parts.append(evt.result.text)

    def stop_cb(evt):
        done_event.set()

    def canceled_cb(evt):
        """
        FIX: Canceled fires for both normal end-of-stream AND errors (bad key,
        network failure, etc.). Previously both cases silently returned an empty
        string. Now we capture the error so the main thread can re-raise it.
        """
        details = evt.result.cancellation_details
        if details.reason == speechsdk.CancellationReason.Error:
            recognition_error.append(
                RuntimeError(
                    f"Azure Speech recognition failed: {details.error_details}"
                )
            )
        done_event.set()

    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=config.AZURE_SPEECH_KEY,
            region=config.AZURE_SPEECH_REGION,
        )
        speech_config.speech_recognition_language = "en-US"

        audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
        speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        speech_recognizer.recognized.connect(recognized_cb)
        speech_recognizer.session_stopped.connect(stop_cb)
        speech_recognizer.canceled.connect(canceled_cb)

        speech_recognizer.start_continuous_recognition()

        # FIX: Use threading.Event.wait() with a timeout instead of a busy-sleep
        # loop. This is cleaner and avoids the race window between break and finally.
        completed = done_event.wait(timeout=TRANSCRIPTION_TIMEOUT)

        if not completed:
            # Timeout — stop the recognizer before raising so Azure cleans up
            try:
                speech_recognizer.stop_continuous_recognition()
            except Exception:
                pass
            raise TimeoutError(
                f"Transcription timed out after {TRANSCRIPTION_TIMEOUT}s. "
                "Partial transcript may be incomplete."
            )

        # Re-raise any error captured in the canceled callback
        if recognition_error:
            raise recognition_error[0]

        return " ".join(transcript_parts).strip()

    finally:
        if speech_recognizer:
            try:
                speech_recognizer.stop_continuous_recognition()
            except Exception as e:
                print(f"Warning: could not stop recognizer cleanly: {e}")
            del speech_recognizer

        # Clean up the converted WAV to avoid filling up disk space
        if tmp_created and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception as e:
                print(f"Cleanup warning (wav): {e}")