.PHONY: setup-githooks

setup-githooks:
	git config core.hooksPath .githooks
	@echo "Git hooks configured to use .githooks/"
