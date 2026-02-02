# --- STAGE 1 "Builder" ---
FROM debian:12-slim AS builder

# Build-Abhängigkeiten
RUN apt-get update && apt-get install -y \
	git \
	build-essential pkg-config checkinstall autoconf automake \
	libtool-bin libssl-dev libcurl4-openssl-dev \
	libavahi-client-dev \
	libusb-1.0-0-dev \
	clang \
	python3

WORKDIR /app

# Kompilierungs-Prozess
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

# Wichtig: ldconfig laufen lassen, damit die Pfade stimmen
RUN ldconfig

# --- STAGE 2: "Finales" Image ---
FROM debian:12-slim

# Installiere NUR die Laufzeit-Abhängigkeiten
RUN apt-get update && apt-get install -y \
	python3 \
	libssl3 \
	libcurl4 \
	libavahi-client3 \
	libusb-1.0-0 \
	procps \
	&& rm -rf /var/lib/apt/lists/*

# === Multi-Stage ===
# Kopiere die kompilierten Bibliotheken & Binaries aus der "builder" Stage
COPY --from=builder /usr/local/lib/lib*.so* /usr/local/lib/
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/local/sbin /usr/local/sbin

# Aktualisiere den Library-Cache im finalen Image
RUN ldconfig

WORKDIR /app

# Port und App starten
ENV PORT=89
CMD ["python3", "-u", "server.py"]