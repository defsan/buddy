#!/bin/bash
# Install whisper.cpp with HTTP server locally.

set -e

INSTALL_DIR="$HOME/whisper.cpp"
MODEL="large-v3"

echo "📦 Installing whisper.cpp..."

# Clone if not present
if [ ! -d "$INSTALL_DIR" ]; then
    git clone https://github.com/ggerganov/whisper.cpp.git "$INSTALL_DIR"
else
    cd "$INSTALL_DIR" && git pull
fi

cd "$INSTALL_DIR"

# Build with Metal (Apple Silicon GPU acceleration)
cmake -B build -DWHISPER_METAL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)

# Download model
bash models/download-ggml-model.sh $MODEL

echo ""
echo "✅ whisper.cpp installed at $INSTALL_DIR"
echo ""
echo "To run the HTTP server:"
echo "  cd $INSTALL_DIR"
echo "  ./build/bin/whisper-server -m models/ggml-$MODEL.bin --host 127.0.0.1 --port 8178"
echo ""
echo "Test:"
echo "  curl http://127.0.0.1:8178/inference -F file=@test.wav"
