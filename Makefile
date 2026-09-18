# 防詐 Copilot — 指令縮寫表（說明書 S2）
# 加指令前先 pull，而且只加不改別人的（§1.5）。

MODULE ?= _template
PY     := uv run

.DEFAULT_GOAL := help

.PHONY: help install run test eval index selfcheck check fmt clean

help:  ## 列出所有指令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## 建環境、裝套件、掛上 pre-commit
	uv sync --extra dev --extra ui
	$(PY) pre-commit install

run:  ## 啟動介面。make run MODULE=a_tbd ／ MODULE=all
	$(PY) streamlit run app/ui.py -- --module=$(MODULE)

test:  ## 跑全部測試
	$(PY) pytest -q

eval:  ## 算某個模組的分數。make eval MODULE=a_tbd
	$(PY) python -m app.cli eval --module=$(MODULE)

index:  ## 建某個模組的向量庫（不進版控，各自本機建）
	$(PY) python -m app.cli index --module=$(MODULE)

selfcheck:  ## S16 入場檢查七項。make selfcheck MODULE=a_tbd
	$(PY) python -m app.cli selfcheck --module=$(MODULE)

check:  ## 越界檢查：模組有沒有偷寫共用三樣、或 import 別人的模組
	$(PY) python tools/check_boundaries.py

fmt:  ## 格式化
	$(PY) ruff format .
	$(PY) ruff check --fix .

clean:  ## 清掉快取
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
