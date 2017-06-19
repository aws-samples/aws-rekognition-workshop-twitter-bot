#!/usr/bin/env bash -xe
if [ -z "$1" ]; then
   echo "missing zip argument homeboy"; exit 1
fi
mkdir -p .tmp_build
cd .tmp_build
if [ ! -d "venv" ]; then
    echo "no virtualenv detected, creating..."
    virtualenv venv > /dev/null
fi
if [ ! -d "PIL" ]; then
    echo "downloading PIL.."
    curl -Lo pillow.tar.gz https://github.com/Miserlou/lambda-packages/blob/master/lambda_packages/Pillow/python2.7-Pillow-3.4.2.tar.gz?raw=true
    tar xzf pillow.tar.gz
    rm pillow.tar.gz
fi
echo "activating virtualenv..."
source venv/bin/activate
echo "installing requirements..."
pip install -t . -r ../requirements.txt > /dev/null
echo "zipping file"
zip -9 -r ../$1 . -x venv/\* > /dev/null
deactivate
cd ..
zip -9 $1 ${@:2}
