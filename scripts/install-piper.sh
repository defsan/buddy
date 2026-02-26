#!/bin/bash
# Install Piper TTS on Mac Mini (the Buddy server host).

set -e

INSTALL_DIR="$HOME/.local/share/piper"
VOICE="en_US-amy-medium"

echo "📦 Installing Piper TTS..."

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Download Piper binary for macOS ARM64
# Check https://github.com/rhasspy/piper/releases for latest
PIPER_VERSION="2023.11.14-2"
ARCH="macos_aarch64"

if [ ! -f "piper/piper" ]; then
    echo "Downloading Piper binary..."
    curl -L -o piper.tar.gz \
        "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_${ARCH}.tar.gz"
    tar xzf piper.tar.gz
    rm piper.tar.gz
    chmod +x piper/piper
    echo "✅ Piper binary installed"
else
    echo "Piper binary already exists"
fi

# Download voice model
VOICES_DIR="$INSTALL_DIR/voices"
mkdir -p "$VOICES_DIR"

if [ ! -f "$VOICES_DIR/${VOICE}.onnx" ]; then
    echo "Downloading voice model: $VOICE..."
    curl -L -o "$VOICES_DIR/${VOICE}.onnx" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx"
    curl -L -o "$VOICES_DIR/${VOICE}.onnx.json" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json"
    echo "✅ Voice model downloaded"
else
    echo "Voice model already exists"
fi

echo ""
echo "✅ Piper TTS installed at $INSTALL_DIR"
echo ""
echo "Test:"
echo "  echo 'Hello, this is a test.' | $INSTALL_DIR/piper/piper --model $VOICES_DIR/${VOICE}.onnx --output_file /tmp/test.wav"
echo "  afplay /tmp/test.wav"
