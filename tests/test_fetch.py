import pytest
from helpers import mock_transport

from waypoint.gather.fetch import Fetcher, FetchError

ARTICLE = "<html><head><title>T</title></head><body><article><h1>Bees count</h1>" + "".join(
    f"<p>Paragraph {i}: honeybees can learn to count small numbers of landmarks on the way "
    f"to food, researchers found in a long series of careful experiments.</p>"
    for i in range(30)) + "</article></body></html>"


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def fetcher(cfg, routes, log=None, clock=None, cache_dir=None):
    clock = clock or Clock()
    return Fetcher(cfg.gather, cache_dir, transport=mock_transport(routes, log),
                   clock=clock, sleep=clock.sleep)


def test_user_agent_names_the_repo(cfg):
    assert "github.com/mbutler98/waypoint-daily" in cfg.gather.user_agent


def test_article_extracts_main_text(cfg):
    f = fetcher(cfg, {"https://news.example/robots.txt": (404, ""),
                      "https://news.example/a": (200, ARTICLE)})
    text, final = f.article("https://news.example/a")
    assert "honeybees" in text and len(text.split()) > 300
    assert final == "https://news.example/a"


def test_robots_disallow_is_respected(cfg):
    robots = "User-agent: *\nDisallow: /private/\n"
    f = fetcher(cfg, {"https://news.example/robots.txt": (200, robots),
                      "https://news.example/private/a": (200, ARTICLE)})
    with pytest.raises(FetchError, match="robots"):
        f.article("https://news.example/private/a")


def test_one_request_per_second_per_host(cfg):
    clock = Clock()
    log: list[str] = []
    routes = {"https://a.example/robots.txt": (404, ""), "https://b.example/robots.txt": (404, ""),
              "https://a.example/1": (200, "x"), "https://a.example/2": (200, "x"),
              "https://b.example/1": (200, "x")}
    f = fetcher(cfg, routes, log, clock)
    f.get_text("https://a.example/1")  # robots + page: 1 wait
    f.get_text("https://b.example/1")  # other host: robots + page: 1 wait
    f.get_text("https://a.example/2")  # a again: last a request was >= 1 s ago on the clock
    assert clock.sleeps and all(s <= cfg.gather.per_host_delay_s for s in clock.sleeps)
    assert len(clock.sleeps) == 2
    assert log.count("https://a.example/robots.txt") == 1  # robots cached per host


def test_paywalled_hosts_are_skipped_without_a_request(cfg):
    log: list[str] = []
    f = fetcher(cfg, {}, log)
    with pytest.raises(FetchError, match="paywall"):
        f.article("https://www.nytimes.com/2026/10/16/science/x.html")
    assert log == []


def test_http_errors_raise(cfg):
    f = fetcher(cfg, {"https://a.example/robots.txt": (404, "")})
    with pytest.raises(FetchError, match="404"):
        f.get_text("https://a.example/missing")


def test_cache_avoids_refetch(cfg, tmp_path):
    log: list[str] = []
    routes = {"https://a.example/robots.txt": (404, ""), "https://a.example/1": (200, "body")}
    for _ in range(2):
        f = fetcher(cfg, routes, log, cache_dir=tmp_path)
        assert f.get_text("https://a.example/1")[0] == "body"
    assert log.count("https://a.example/1") == 1
