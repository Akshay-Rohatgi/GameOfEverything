FROM mcr.microsoft.com/playwright:v1.47.0-jammy

# Install socat for TCP forwarding (Chrome only binds CDP to localhost)
RUN apt-get update && apt-get install -y socat && rm -rf /var/lib/apt/lists/*

EXPOSE 9222

# Chrome binds CDP to 127.0.0.1 only, so we use socat to forward 0.0.0.0:9222 → 127.0.0.1:9223
# Disable ALL security features to allow navigation to Docker internal hostnames
# These flags are necessary because Chrome otherwise blocks navigation to
# internal network addresses when controlled via CDP from outside the container
CMD socat TCP-LISTEN:9222,reuseaddr,fork TCP:127.0.0.1:9223 & \
    exec /ms-playwright/chromium-1134/chrome-linux/chrome \
     --headless=new \
     --no-sandbox \
     --disable-gpu \
     --disable-dev-shm-usage \
     --disable-software-rasterizer \
     --disable-web-security \
     --disable-features=IsolateOrigins,site-per-process,BlockInsecurePrivateNetworkRequests \
     --disable-site-isolation-trials \
     --allow-insecure-localhost \
     --unsafely-treat-insecure-origin-as-secure=http://target:3000 \
     --remote-debugging-port=9223 \
     --remote-allow-origins='*' \
     --user-data-dir=/tmp/chrome-profile \
     about:blank
