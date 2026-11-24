# Интеграции LoomHarness

Переносимое ядро (компактный ввод-вывод, доска команды, MCP-сервер) работает в любом MCP-способном агенте. Хуки с гейтом доказательств подключены к терминальным CLI напрямую, к остальным — через мосты в этом каталоге.

| Среда | Механизм | Гейт правок | Подсказки в промпт | Статус |
|---|---|---|---|---|
| Codex CLI | `install.py --cli codex` | полный | полный | проверено тестами |
| Codex Desktop (ChatGPT app) | общий `~/.codex` с CLI | полный | полный | подтверждено докой OpenAI |
| Claude Code | `install.py --cli claude` | полный (Edit/Write/MultiEdit) | полный | проверено симуляцией событий |
| Claude Desktop (вкладка Code) | общий `~/.claude` с CLI | полный | полный | подтверждено докой Anthropic |
| Gemini CLI | `install.py --cli gemini` | полный (write_file/replace) | полный | проверено симуляцией событий |
| Antigravity (десктоп Google) | `install.py --cli antigravity` | полный | только старт/стоп | по докам Google, нужна живая проверка |
| OpenCode | `integrations/opencode` | через мост | через мост | доставка подсказки проверена в OpenCode 1.18.0 на macOS, контракты хуков 1.18 и 2.4 проверяются в CI |
| DeepSeek Harness | навык `SKILL.md` + `integrations/dsh` (MCP-бандл) | нет локальных хуков | навык работает | MCP-бандл: конфигурация |
| Чат Claude Desktop | `claude_desktop_config.json` (MCP) | нет | нет | MCP-сервер, конфиг вручную |
| Чат Gemini Desktop | remote MCP по HTTPS | нет | нет | нужна HTTP-обёртка над stdio |
| Прочие MCP-агенты | `mcp_server.py` | нет | нет | инструменты чтения и карты кода |

Десктопные Codex и Claude исполняют те же локальные command-хуки, что и их CLI (общие каталоги настроек) — отдельная установка не нужна. Источники: [Codex Hooks](https://developers.openai.com/codex/hooks) (раздел про desktop app), [Claude Code Hooks](https://code.claude.com/docs/en/hooks) («the same hook events wherever it runs… the Desktop app»), [Antigravity Hooks](https://antigravity.google/docs/hooks).

## Длинные сессии

Для настройки штатного сжатия в Codex, Claude Code и Gemini CLI, получения бюджета через MCP и сборки нового ввода в собственном клиенте см. [раннее сжатие истории](../loomharness/references/history.md). Для адаптера достаточно знать окно выбранной модели и размер активного ввода.

## OpenCode

Установите ядро через `install.py`. В конфигурации проекта укажите каталог моста из этого репозитория. В OpenCode 1.18 используйте `"plugin": ["file:///absolute/path/to/integrations/opencode"]`, в OpenCode 2.x — `"plugins": [{"package": "./integrations/opencode"}]` с путём относительно конфигурации. [Переход между версиями OpenCode](https://opencode.ai/docs/build/plugins/migrate-v1)

Отправьте в тестовом проекте `LoomHarness on`. Попытка отредактировать `state.json` до чтения доказательства должна вернуть причину отказа. Для чтения выполните `harness.py read <путь-к-логу>` в каталоге проекта, передав через переменную окружения `CODEX_THREAD_ID` идентификатор этой сессии OpenCode. Затем повторите правку в той же сессии.

Мост по умолчанию вызывает `~/.agents/skills/loomharness/scripts/harness.py`. Для другой установки задайте `LOOMHARNESS_HELPER`, а для выбора интерпретатора — `LOOMHARNESS_PYTHON`.

## DeepSeek Harness

- Скопируйте каталог `loomharness/` с файлом `SKILL.md` в каталог навыков пресета.
- Бандл `integrations/dsh` подключает пять инструментов из `mcp_server.py` для чтения файлов, поиска, карты кода, метрик и бюджета контекста. Замените `<HELPER>` в `cordis.patch.yml` на абсолютный путь к `scripts/mcp_server.py`, затем установите бандл через Plugins → Add plugin.
