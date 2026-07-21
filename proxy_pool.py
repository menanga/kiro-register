"""Proxy pool manager with failure tracking and automatic blacklisting."""
import logging
import os
import random
import threading
from typing import Optional

logger = logging.getLogger("proxy-pool")


class ProxyPool:
    """Thread-safe proxy pool with failure tracking.

    Proxies failing 3 times are blacklisted. When all proxies dead, returns None.
    """

    def __init__(self, proxies: list[str]):
        """
        Args:
            proxies: List of proxy strings in format "IP:PORT" or "http://IP:PORT"
        """
        self._lock = threading.Lock()
        self._proxies: dict[str, int] = {}  # proxy_url -> failure_count

        # Parse and normalize proxies
        for proxy in proxies:
            proxy = proxy.strip()
            if not proxy:
                continue
            # Add http:// prefix if missing
            if not proxy.startswith("http://") and not proxy.startswith("socks"):
                proxy = f"http://{proxy}"
            self._proxies[proxy] = 0

        logger.info(f"Proxy pool initialized with {len(self._proxies)} proxies")

    def get_random_proxy(self) -> Optional[str]:
        """Get a random working proxy, or None if all are blacklisted."""
        with self._lock:
            # Filter out blacklisted (failure_count >= 3)
            available = [p for p, fails in self._proxies.items() if fails < 3]

            if not available:
                logger.warning("All proxies blacklisted (3+ failures) - running without proxy")
                return None

            proxy = random.choice(available)
            logger.debug(f"Selected proxy: {proxy} (failures: {self._proxies[proxy]})")
            return proxy

    def report_failure(self, proxy: str):
        """Increment failure count for a proxy. Blacklisted at 3 failures."""
        if not proxy:
            return

        with self._lock:
            if proxy in self._proxies:
                self._proxies[proxy] += 1
                logger.warning(
                    f"Proxy failed: {proxy} (total failures: {self._proxies[proxy]}/3)"
                )

                if self._proxies[proxy] >= 3:
                    logger.error(f"Proxy blacklisted after 3 failures: {proxy}")

    def report_success(self, proxy: str):
        """Report successful use (keeps failure history for tracking)."""
        if not proxy:
            return

        with self._lock:
            if proxy in self._proxies:
                # Don't reset - keep failure history
                # This prevents a proxy from being re-used after 2 failures + 1 success
                logger.debug(f"Proxy used successfully: {proxy} (failures: {self._proxies[proxy]})")

    def get_stats(self) -> dict:
        """Get current pool statistics."""
        with self._lock:
            total = len(self._proxies)
            blacklisted = sum(1 for fails in self._proxies.values() if fails >= 3)
            available = total - blacklisted

            return {
                "total": total,
                "available": available,
                "blacklisted": blacklisted,
                "proxies": dict(self._proxies),
            }


def load_proxy_pool_from_env() -> Optional[ProxyPool]:
    """Load proxy pool from PROXIES env variable.

    Format: PROXIES=IP:PORT,IP:PORT,IP:PORT
    Example: PROXIES=129.222.204.27:10000,152.22.72.24:80

    Returns:
        ProxyPool instance if PROXIES set, None otherwise
    """
    proxies_env = os.environ.get("PROXIES", "").strip()

    if not proxies_env:
        logger.info("PROXIES env not set - running without proxy pool")
        return None

    # Parse comma-separated list
    proxy_list = [p.strip() for p in proxies_env.split(",") if p.strip()]

    if not proxy_list:
        logger.warning("PROXIES env empty after parsing - running without proxy pool")
        return None

    return ProxyPool(proxy_list)
