#!/bin/bash

echo "🧹 Step 1: Cleaning previous builds..."
rm -rf build/ dist/ run.spec
echo "✔ Cleaned build directories and spec file."

echo "🔌 Step 2: Activating virtual environment..."
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "✔ Virtual environment activated."
else
    echo "❌ Error: Virtual environment 'venv' not found. Please run 'python -m venv venv' first."
    exit 1
fi

echo "🔨 Step 3: Building the executable with PyInstaller..."
# We include the hidden import for pygame.font as discussed
pyinstaller --onefile --windowed --hidden-import pygame.font run.py

if [ $? -eq 0 ]; then
    echo "✅ Build successful! Your executable is located in the 'dist/' folder."
else
    echo "❌ Build failed. Check the error logs above."
    # Deactivate before exiting on failure
    deactivate
    exit 1
fi

echo "🔌 Step 4: Deactivating virtual environment..."
deactivate
echo "🎉 All done!"

