"""
Generates a spoken audio version of the Hinglish payment reminder.

Uses gTTS (Google Text-to-Speech) -- free, no API key required. Honest
limitation worth knowing: gTTS reads Roman-script Hinglish with Hindi
phonetics, which sounds reasonable but not perfectly native -- a production
system would use a proper Indian-language TTS provider (e.g. Bhashini,
ElevenLabs, or Twilio's Indian voice options) for better pronunciation.
This is real, working voice output, not a mockup -- just not
production-polish audio quality.

If gTTS fails (no network, quota, or any other reason), this returns None
and the caller falls back gracefully -- voice is an enhancement, never a
blocker for the core text-based recovery flow.
"""
import io


def synthesize_hinglish_voice(text: str) -> bytes | None:
    try:
        from gtts import gTTS
        # lang='hi' gives Hindi phonetics, which reads Hinglish (Roman-script
        # Hindi/English mix) far more naturally than lang='en' would.
        tts = gTTS(text=text, lang="hi", slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        return buf.getvalue()
    except Exception as e:
        print(f"[voice] TTS synthesis failed (non-fatal, continuing without audio): {e}")
        return None