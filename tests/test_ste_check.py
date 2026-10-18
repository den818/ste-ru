#!/usr/bin/env python3
"""Unit-тесты линтера ste_check.py. Запуск: python3 -m unittest discover -s tests (из каталога скилла)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "ste_check.py")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import ste_check  # noqa: E402


def run(text, *args):
    p = subprocess.run([sys.executable, SCRIPT, "-", "--json"] + list(args), input=text, capture_output=True, text=True)
    data = json.loads(p.stdout) if p.stdout.strip() else None
    return p.returncode, data, p.stderr


def rules(data):
    return [i["rule"] for i in data["issues"]]


class ExitCodes(unittest.TestCase):
    def test_warning_does_not_fail_by_default(self):
        code, data, _ = run("Необходимо проверить сервис.\n", "--mode", "procedural")
        self.assertIn("модальность", rules(data))
        self.assertEqual(code, 0)

    def test_strict_fails_on_warning(self):
        code, data, _ = run("Необходимо проверить сервис.\n", "--mode", "procedural", "--strict")
        self.assertEqual(code, 1)
        self.assertEqual(data["exit_code"], 1)

    def test_fail_on_warning_alias(self):
        code, _, _ = run("Необходимо проверить сервис.\n", "--fail-on", "warning")
        self.assertEqual(code, 1)

    def test_error_fails_with_default_threshold(self):
        code, data, _ = run("Проверьте сервис; затем перезапустите его.\n", "--mode", "procedural")
        self.assertIn("точка с запятой", rules(data))
        self.assertEqual(code, 1)

    def test_clean_text_exit_zero(self):
        code, data, _ = run("Проверьте логи сервиса.\n", "--mode", "procedural", "--strict")
        self.assertEqual(code, 0)
        self.assertEqual(data["issues"], [])

    def test_missing_file_exit_2(self):
        p = subprocess.run([sys.executable, SCRIPT, "/nonexistent/file.md"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_bad_argument_exit_2(self):
        p = subprocess.run([sys.executable, SCRIPT, "-", "--mode", "wrong"], input="", capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)


class Markdown(unittest.TestCase):
    def test_closed_frontmatter_is_skipped(self):
        code, data, _ = run("---\ntitle: необходимо\n---\nПроверьте логи.\n", "--mode", "procedural", "--strict")
        self.assertEqual(data["stats"]["sentences"], 1)
        self.assertEqual(code, 0)

    def test_unclosed_frontmatter_is_analyzed_as_text(self):
        code, data, err = run("---\ntitle: test\nНеобходимо выполнить проверку сервиса.\n", "--mode", "procedural", "--strict")
        self.assertGreaterEqual(data["stats"]["sentences"], 1)
        self.assertIn("модальность", rules(data))
        self.assertIn("не закрыта", err)
        self.assertEqual(code, 2)   # незакрытая разметка — неверный вход

    def test_backtick_and_tilde_fences_are_skipped(self):
        for fence in ("```", "~~~"):
            text = "%s\nНеобходимо осуществить проверку; данный код.\n%s\nПроверьте логи.\n" % (fence, fence)
            _, data, _ = run(text, "--mode", "procedural", "--strict")
            self.assertEqual(data["issues"], [], fence)
            self.assertEqual(data["stats"]["sentences"], 1, fence)

    def test_unclosed_fence_is_analyzed_and_fails(self):
        """Незакрытый блок кода не может дать успешный пустой результат даже без --strict."""
        code, data, err = run("```\nНеобходимо проверить сервис.\n", "--mode", "procedural", "--strict")
        self.assertEqual(data["stats"]["sentences"], 1)
        self.assertIn("модальность", rules(data))
        self.assertIn("не закрыт", err)
        self.assertEqual(code, 2)
        self.assertEqual(data["exit_code"], 2)

    def test_unclosed_fence_fails_without_strict(self):
        code, _, _ = run("```\nПроверьте логи.\n", "--mode", "procedural")
        self.assertEqual(code, 2)

    def test_longer_fence_not_closed_by_shorter(self):
        text = "````\nкод; данный\n```\nещё код\n````\nНеобходимо проверить сервис.\n"
        code, data, _ = run(text, "--mode", "descriptive")
        self.assertEqual(data["stats"]["sentences"], 1)
        self.assertIn("модальность", rules(data))
        self.assertNotEqual(code, 2)

    def test_tilde_fence_length_symmetry(self):
        for ch in ("`", "~"):
            text = "%s\nкод; данный\n%s\nещё код\n%s\nПроверьте логи.\n" % (ch * 4, ch * 3, ch * 4)
            code, data, _ = run(text, "--mode", "procedural", "--strict")
            self.assertEqual(data["stats"]["sentences"], 1, ch)
            self.assertEqual(code, 0, ch)

    def test_fence_with_language_does_not_close(self):
        """Строка с языком после маркера — открывающий, но не закрывающий маркер."""
        text = "```\nкод\n```python\nНеобходимо проверить сервис.\n```\n"
        code, data, _ = run(text, "--mode", "procedural", "--strict")
        self.assertEqual(data["stats"]["sentences"], 0)
        self.assertEqual(code, 0)

    def test_fence_with_text_suffix_does_not_close(self):
        text = "~~~\nкод\n~~~ text\nНеобходимо проверить сервис.\n~~~\n"
        code, data, _ = run(text, "--mode", "procedural", "--strict")
        self.assertEqual(data["stats"]["sentences"], 0)
        self.assertEqual(code, 0)

    def test_real_closer_may_have_trailing_spaces(self):
        text = "```\nкод\n```   \nПроверьте логи.\n"
        code, data, _ = run(text, "--mode", "procedural", "--strict")
        self.assertEqual(data["stats"]["sentences"], 1)
        self.assertEqual(code, 0)

    def test_fence_of_other_char_does_not_close(self):
        text = "```\nкод; данный\n~~~\nещё код\n```\nПроверьте логи.\n"
        code, data, _ = run(text, "--mode", "procedural", "--strict")
        self.assertEqual(data["stats"]["sentences"], 1)
        self.assertEqual(code, 0)

    def test_inline_code_kept_in_issue_text(self):
        _, data, _ = run("Необходимо запустить `make test` перед выкаткой.\n", "--mode", "procedural")
        self.assertTrue(data["issues"])
        self.assertIn("`make test`", data["issues"][0]["text"])

    def test_inline_code_not_scanned_for_patterns(self):
        _, data, _ = run("Запустите `git commit --amend; данный`.\n", "--mode", "procedural")
        self.assertEqual(data["issues"], [])

    def test_sentence_split_after_closing_quote(self):
        for pair in (("Сервис вернул «Ошибка!» Перезапустите сервис.", 2),
                     ("Сервис вернул «Готово?» Проверьте логи.", 2),
                     ('Он ответил "Готово." Проверьте логи.', 2),
                     ("Сервис вернул «429 Too Many Requests» клиенту.", 1)):
            _, data, _ = run(pair[0] + "\n", "--mode", "descriptive")
            self.assertEqual(data["stats"]["sentences"], pair[1], pair[0])

    def test_heading_is_never_a_sentence(self):
        for text in ("# Заголовок\nСервис хранит счётчики в Redis.\n", "# Заголовок\n\nСервис хранит счётчики в Redis.\n"):
            _, data, _ = run(text, "--mode", "descriptive")
            self.assertEqual(data["stats"]["sentences"], 1, text)

    def test_table_rows_are_skipped(self):
        _, data, _ = run("| Что | Как |\n|---|---|\n| необходимо | является |\n", "--mode", "descriptive")
        self.assertEqual(data["stats"]["sentences"], 0)

    def test_inline_code_with_spaces_is_one_word(self):
        self.assertEqual(ste_check.count_words("Запустите `make check` сейчас."), 3)

    def test_empty_and_code_only(self):
        for text in ("", "```\nx = 1\n```\n"):
            code, data, _ = run(text, "--strict")
            self.assertEqual(code, 0)
            self.assertEqual(data["stats"]["sentences"], 0)


class WordCount(unittest.TestCase):
    GOLDEN = [
        ("Проверьте логи сервиса.", 3),
        ("Запустите `make check` сейчас.", 3),
        ("Установите веб-сервер и API-ключ.", 4),
        ("Подождите 15 мин.", 2),
        ("Лимит — 10 %.", 2),
        ("Нагрузка 10 000 RPS.", 2),
        ("Пауза 10–15 мин между попытками.", 4),
        ("Откройте /etc/orders/config.yaml.", 2),
        ("Смотрите https://wiki.example.com/orders сейчас.", 3),
        ("Задача PROJ-1234 закрыта.", 3),
        ("Версия 2.1.0 вышла.", 3),
        ("Сервис вернул «429 Too Many Requests» клиенту.", 4),
        ("В логе строка «connection reset by peer after timeout while waiting».", 4),
        ("Нажмите **Сохранить** и закройте окно.", 5),
        ("Задайте таймаут (по умолчанию 30 с) в конфиге.", 5),
        ("Мы используем Google Cloud для сборки.", 5),
        ("Иван Петров проверил Google Диск.", 3),
        ("Redis хранит данные.", 3),
        ("Число 0,5 и число 3.", 5),
        ("PR 4521 влит.", 2),
    ]

    def test_golden_table(self):
        for sentence, expected in self.GOLDEN:
            self.assertEqual(ste_check.count_words(sentence), expected, sentence)

    def test_glossary_term_is_one_word(self):
        regs = ste_check.glossary_regexes(["очередь задач", "дежурный по сервису"])
        self.assertEqual(ste_check.count_words("Очистите очередь задач сейчас.", regs), 3)
        self.assertEqual(ste_check.count_words("Дежурному по сервису нужен доступ.", regs), 3)

    def test_parenthetical_content_has_own_limit(self):
        long_paren = "(" + " ".join(["слово"] * 26) + ")"
        _, data, _ = run("Задайте таймаут %s в конфиге.\n" % long_paren, "--mode", "descriptive")
        self.assertIn("длинная скобочная вставка", rules(data))
        self.assertNotIn("длина предложения", rules(data))


class Lists(unittest.TestCase):
    def test_bullet_preconditions_not_flagged(self):
        _, data, _ = run("- Нужен доступ к Vault.\n- Права на выкатку.\n- Доступ к логам.\n", "--mode", "procedural")
        self.assertNotIn("пункт не с команды", rules(data))

    def test_numbered_non_command_flagged(self):
        _, data, _ = run("1. Проверка сервиса.\n2. Запустите миграцию.\n", "--mode", "procedural")
        self.assertEqual(rules(data).count("пункт не с команды"), 1)

    def test_bullet_actions_with_one_odd_item_flagged(self):
        _, data, _ = run("- Остановите воркер.\n- Очистите очередь.\n- Перезапуск сервиса.\n", "--mode", "procedural")
        self.assertEqual(rules(data).count("пункт не с команды"), 1)

    def test_condition_start_is_ok(self):
        _, data, _ = run("1. Если тесты падают, исправьте их.\n2. Запустите сборку.\n", "--mode", "procedural")
        self.assertNotIn("пункт не с команды", rules(data))

    def test_nested_list_does_not_disable_parent_check(self):
        _, data, _ = run("1. Проверка сервиса:\n   - логи\n   - метрики\n", "--mode", "procedural")
        flagged = [i for i in data["issues"] if i["rule"] == "пункт не с команды"]
        self.assertEqual(len(flagged), 1)
        self.assertIn("Проверка сервиса", flagged[0]["text"])

    def test_nested_enumeration_not_flagged(self):
        text = "1. Создайте PR. В описании PR укажите:\n   - номер тикета\n   - краткое описание проблемы\n2. Отправьте PR на ревью.\n3. Слейте PR в main.\n"
        _, data, _ = run(text, "--mode", "procedural")
        self.assertNotIn("пункт не с команды", rules(data))

    def test_enumeration_after_colon_not_flagged(self):
        text = "Во время выкатки следите за метриками:\n\n- число ответов 5xx\n- тайминги\n- ошибки в логах\n"
        _, data, _ = run(text, "--mode", "procedural")
        self.assertNotIn("пункт не с команды", rules(data))

    def test_list_is_not_a_paragraph(self):
        text = "\n".join("%d. Шаг номер %d." % (i, i) for i in range(1, 9)) + "\n"
        _, data, _ = run(text, "--mode", "procedural")
        self.assertNotIn("длина абзаца", rules(data))


class Address(unittest.TestCase):
    def test_ty_forms_flagged_in_vy_mode(self):
        _, data, _ = run("1. Перезапусти сервис.\n2. Проверь логи.\n", "--mode", "procedural")
        self.assertEqual(rules(data).count("пункт не с команды"), 2)

    def test_ty_forms_accepted_in_ty_mode(self):
        _, data, _ = run("1. Перезапусти сервис.\n2. Проверь логи.\n3. Открой дашборд.\n", "--mode", "procedural", "--address", "ty")
        self.assertNotIn("пункт не с команды", rules(data))

    def test_auto_detects_ty(self):
        _, data, _ = run("1. Перезапусти сервис.\n2. Проверь логи.\n3. Открой дашборд.\n", "--mode", "procedural", "--address", "auto")
        self.assertEqual(data["address"], "ty")
        self.assertNotIn("пункт не с команды", rules(data))

    def test_auto_single_ty_command(self):
        _, data, _ = run("1. Перезапусти сервис.\n", "--mode", "procedural", "--address", "auto")
        self.assertEqual(data["address"], "ty")
        self.assertNotIn("пункт не с команды", rules(data))

    def test_auto_majority_two_to_one(self):
        _, data, _ = run("1. Перезапусти сервис.\n2. Проверь логи.\n3. Откройте дашборд.\n", "--mode", "procedural", "--address", "auto")
        self.assertEqual(data["address"], "ty")
        self.assertIn("смешение форм обращения", rules(data))

    def test_auto_tie_falls_back_to_vy(self):
        _, data, _ = run("1. Перезапусти сервис.\n2. Откройте дашборд.\n", "--mode", "procedural", "--address", "auto")
        self.assertEqual(data["address"], "vy")
        self.assertIn("смешение форм обращения", rules(data))

    def test_auto_two_to_two_is_tie(self):
        text = "1. Перезапусти сервис.\n2. Проверь логи.\n3. Откройте дашборд.\n4. Закройте окно.\n"
        _, data, _ = run(text, "--mode", "procedural", "--address", "auto")
        self.assertEqual(data["address"], "vy")
        self.assertIn("смешение форм обращения", rules(data))

    def test_auto_single_form_no_mixing(self):
        _, data, _ = run("1. Откройте дашборд.\n2. Закройте окно.\n", "--mode", "procedural", "--address", "auto")
        self.assertNotIn("смешение форм обращения", rules(data))

    def test_mixed_forms_flagged(self):
        _, data, _ = run("1. Перезапустите сервис.\n2. Проверьте логи.\n3. Открой дашборд.\n4. Нажми кнопку.\n", "--mode", "procedural", "--address", "auto")
        self.assertIn("смешение форм обращения", rules(data))


class Terms(unittest.TestCase):
    def test_terms_ignore_inline_code(self):
        code, data, _ = run("Запустите `сервис сервис служба служба`.\n", "--mode", "procedural",
                            "--terms", "сервис,служба", "--strict")
        self.assertNotIn("синонимы для одного понятия", rules(data))
        self.assertEqual(code, 0)

    def test_terms_ignore_quoted_text(self):
        _, data, _ = run("В логе строка «сервис сервис служба служба».\n", "--mode", "descriptive",
                         "--terms", "сервис,служба")
        self.assertNotIn("синонимы для одного понятия", rules(data))

    def test_terms_ignore_second_synonym_only_in_protected(self):
        text = "Сервис отвечает быстро. Сервис перезапущен. В логе строка «служба служба недоступна».\n"
        _, data, _ = run(text, "--mode", "descriptive", "--terms", "сервис,служба")
        self.assertNotIn("синонимы для одного понятия", rules(data))

    def test_terms_flag_conflict_in_prose(self):
        text = "Сервис отвечает быстро. Служба перезапущена. Сервис работает. Служба готова.\n"
        _, data, _ = run(text, "--mode", "descriptive", "--terms", "сервис,служба")
        self.assertIn("синонимы для одного понятия", rules(data))


class Conditions(unittest.TestCase):
    def test_condition_after_command(self):
        _, data, _ = run("Откатите релиз, если ошибки растут.\n", "--mode", "procedural")
        self.assertIn("условие после команды", rules(data))

    def test_condition_before_command(self):
        _, data, _ = run("Если ошибки растут, откатите релиз.\n", "--mode", "procedural")
        self.assertNotIn("условие после команды", rules(data))

    def test_clause_verbs_are_exempt(self):
        _, data, _ = run("Дождитесь, когда выкатка завершится.\n", "--mode", "procedural")
        self.assertNotIn("условие после команды", rules(data))


class RuleMapping(unittest.TestCase):
    def test_every_pattern_has_ru_and_hint(self):
        for pat, rule, ru, hint in ste_check.PATTERNS:
            self.assertRegex(ru, r"^RU-\d\d$", rule)
            self.assertTrue(hint, rule)

    def test_rule_to_ru_complete(self):
        for rule in ("длина предложения", "пункт не с команды", "условие после команды", "длина абзаца",
                     "синонимы для одного понятия", "смешение форм обращения", "длинная скобочная вставка"):
            self.assertRegex(ste_check.RULE_TO_RU[rule], r"^RU-\d\d$")


if __name__ == "__main__":
    unittest.main()
