# --- STAGE 1 "Builder" ---
FROM debian:12-slim AS builder

RUN apt-get update && apt-get install -y \
	git \
	build-essential pkg-config checkinstall autoconf automake \
	libtool-bin libssl-dev libcurl4-openssl-dev \
	libavahi-client-dev \
	libusb-1.0-0-dev \
	clang \
	python3 \
	python3-pip

WORKDIR /app

# 1. libimobiledevice & usbmuxd2 Kompilierung (Dein bestehender Block)
RUN git clone https://github.com/libimobiledevice/libplist.git && \
	cd libplist && ./autogen.sh && make && make install && \
	cd .. && \
	git clone https://github.com/libimobiledevice/libimobiledevice-glue.git && \
	cd libimobiledevice-glue && ./autogen.sh && make && make install && \
	cd .. && \
	git clone https://github.com/libimobiledevice/libtatsu.git && \
	cd libtatsu && ./autogen.sh && make && make install && \
	cd .. && \
	git clone https://github.com/libimobiledevice/libusbmuxd.git && \
	cd libusbmuxd && ./autogen.sh && make && make install && \
	cd .. && \
	git clone https://github.com/libimobiledevice/libimobiledevice.git && \
	cd libimobiledevice && ./autogen.sh && make && make install && \
	cd .. && \
	git clone https://github.com/tihmstar/libgeneral.git && \
	cd libgeneral && ./autogen.sh && make && make install && \
	cd .. && \
	git clone https://github.com/fosple/usbmuxd2.git && \
	cd usbmuxd2 && ./autogen.sh && ./configure CC=clang CXX=clang++ && make && make install && \
	cd ..

# 2. Python-Abhängigkeiten in einen lokalen Ordner installieren
RUN pip3 install --break-system-packages --no-cache-dir --target=/app/python_libs \
    uvicorn \
    fastapi

# --- STAGE 2: "Finales" Image ---
FROM debian:12-slim

# Nur Runtime-Abhängigkeiten
RUN apt-get update && apt-get install -y \
	python3 \
	libssl3 \
	libcurl4 \
	libavahi-client3 \
	libusb-1.0-0 \
	procps \
	&& rm -rf /var/lib/apt/lists/*

# Kopiere kompilierte C-Bibliotheken & Binaries
COPY --from=builder /usr/local/lib/lib*.so* /usr/local/lib/
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/local/sbin /usr/local/sbin

# Kopiere die Python-Pakete
COPY --from=builder /app/python_libs /usr/local/lib/python3/dist-packages

# Library Cache aktualisieren
RUN ldconfig

WORKDIR /app

# Startbefehl via Python-Modul-Aufruf
ENTRYPOINT ["start.sh"]