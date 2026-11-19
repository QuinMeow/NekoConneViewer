#!/usr/bin/env bash
# Run inside a native shell (MSYS2 UCRT64 on Windows) after media.py --prepare.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.tools/media-build" && pwd)"
recipe="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
export PATH="$root/usr/bin:$PATH"
prefix="$root/prefix"
threads="${BUILD_JOBS:-6}"
mkdir -p "$root"/{bin,b-ffmpeg,b-vpx} "$prefix"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) ;; *) echo "Use MSYS2 UCRT64 on Windows x64" >&2; exit 1 ;; esac
target=x86_64-win64-gcc
ext=.exe

cmake -S "$root/opus-1.5.2" -B "$root/b-opus" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=OFF \
  -DOPUS_BUILD_PROGRAMS=OFF -DOPUS_BUILD_TESTING=OFF -DOPUS_INSTALL_PKG_CONFIG_MODULE=ON
cmake --build "$root/b-opus" --parallel "$threads"
cmake --install "$root/b-opus"

cd "$root/b-vpx"
if [[ ! -f Makefile ]]; then
  "$root/libvpx-1.15.2/configure" --target="$target" --prefix="$prefix" --disable-shared \
    --enable-static --disable-examples --disable-tools --disable-unit-tests --disable-docs --disable-vp8
fi
make -j"$threads"
make install

cd "$root/b-ffmpeg"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
if [[ ! -f config.h || "$recipe" -nt config.h ]]; then
  "$root/ffmpeg-8.1.1/configure" --disable-autodetect --disable-everything --disable-network \
    --disable-doc --disable-debug --disable-ffplay --disable-ffprobe --disable-shared --enable-static \
    --pkg-config-flags=--static --enable-libopus --enable-libvpx --enable-ffmpeg \
    --enable-protocol=file,pipe --enable-demuxer=ivf,matroska,rawvideo,wav,image2,image2pipe,hca \
    --enable-muxer=webm,matroska,ivf,rawvideo,wav,image2,image2pipe,null,framehash,framemd5 \
    --enable-decoder=vp9,libvpx_vp9,rawvideo,pcm_s16le,pcm_f32le,hca,opus,libopus,mjpeg \
    --enable-encoder=libvpx_vp9,libopus,pcm_s16le,rawvideo,mjpeg,wrapped_avframe \
    --enable-parser=vp9,opus,mjpeg --enable-bsf=vp9_superframe,vp9_superframe_split \
    --enable-filter=scale,format,fps,crop,pad,setsar,trim,atrim,setpts,asetpts,aresample,aformat,volume,overlay,unpremultiply,premultiply_dynamic,setparams \
    --extra-ldflags=-static --disable-pthreads --enable-w32threads
fi
make -j"$threads" ffmpeg"$ext"
cp ffmpeg"$ext" "$root/bin/ffmpeg$ext"
"$root/bin/ffmpeg$ext" -hide_banner -version
