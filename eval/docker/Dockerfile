# vague-code 评测基础镜像（ADR-0040）：6 语言工具链 + verifier 运行时
# 构建：docker build --network host -t vague-eval -f eval/docker/Dockerfile .
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    golang-go \
    openjdk-17-jdk-headless \
    g++ cmake make \
    libboost-dev libboost-date-time-dev \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 20 LTS（ubuntu 源 node 12 太老，jest 29 需 >=14）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir -q pytest

# Rust 工具链（rustup 官方安装器，走构建代理）
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup.sh \
    && sh /tmp/rustup.sh -y --profile minimal --default-toolchain stable \
    && rm /tmp/rustup.sh
ENV PATH="/root/.cargo/bin:${PATH}"

RUN cargo --version && rustc --version \
    && go version && node --version && python3 --version \
    && java -version 2>&1 | head -1 && g++ --version | head -1 && cmake --version | head -1
