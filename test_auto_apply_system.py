"""
Unit and integration tests for auto-apply, smart notifications, and HH integration.
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock

# Import functions from bot.py
import bot

class TestAutoApplyAndNotifications(unittest.TestCase):
    def setUp(self):
        self.test_data_dir = os.path.join(os.path.dirname(__file__), "test_data_temp")
        os.makedirs(self.test_data_dir, exist_ok=True)
        bot.DATA_DIR = self.test_data_dir
        bot.STATUSES_FILE = os.path.join(self.test_data_dir, "test_statuses.json")
        bot.CACHE_FILE = os.path.join(self.test_data_dir, "test_cache.json")
        bot.HH_NEGOTIATIONS_FILE = os.path.join(self.test_data_dir, "test_hh_neg.json")
        bot.CONFIG_FILE = os.path.join(self.test_data_dir, "test_config.json")
        bot._config_cache = None
        os.environ["RESUME_QUICK_TUNNEL_URL"] = "https://demo-resume.trycloudflare.com/"
        bot.DEFAULT_CONFIG["resume_quick_tunnel_url"] = "https://demo-resume.trycloudflare.com/"

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_data_dir):
            shutil.rmtree(self.test_data_dir, ignore_errors=True)

    def test_default_config_fields(self):
        cfg = bot.load_config()
        self.assertIn("notify_min_score", cfg)
        self.assertIn("auto_apply_enabled", cfg)
        self.assertIn("auto_apply_min_score", cfg)
        self.assertIn("resume_quick_tunnel_url", cfg)
        self.assertIn("hh_access_token", cfg)
        self.assertIn("hh_resume_id", cfg)
        self.assertEqual(cfg["notify_min_score"], 7)
        self.assertEqual(cfg["auto_apply_min_score"], 7)
        self.assertEqual(cfg["resume_quick_tunnel_url"], "https://demo-resume.trycloudflare.com/")
        self.assertTrue(cfg["auto_apply_enabled"])

    def test_update_vacancy_status_extra_kwargs(self):
        updated = bot.update_vacancy_status(
            "hh_999", "applied",
            cover_letter="Test letter",
            auto_applied=True,
            method="hh_api"
        )
        self.assertEqual(updated["status"], "applied")
        self.assertEqual(updated["cover_letter"], "Test letter")
        self.assertTrue(updated["auto_applied"])
        self.assertEqual(updated["method"], "hh_api")

        loaded = bot.load_statuses()
        self.assertEqual(loaded["hh_999"]["status"], "applied")
        self.assertEqual(loaded["hh_999"]["cover_letter"], "Test letter")

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_api_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {"Location": "/negotiations/12345"}
        mock_post.return_value = mock_resp

        res = bot.apply_hh_vacancy_api("138000000", "resume_xyz", "Письмо", "test_tok")
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "sent")

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_api_already_applied(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"errors": [{"value": "already_applied", "type": "negotiations"}]}
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_post.return_value = mock_resp

        res = bot.apply_hh_vacancy_api("138000000", "resume_xyz", "Письмо", "test_tok")
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "already_applied")

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_api_test_required(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"errors": [{"value": "test_required", "type": "negotiations"}]}
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_post.return_value = mock_resp

        res = bot.apply_hh_vacancy_api("138000000", "resume_xyz", "Письмо", "test_tok")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "test_required")

    @patch("bot.requests.get")
    def test_auto_select_hh_resume(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"id": "res_1", "title": "Курьер", "status": {"id": "published"}},
                {"id": "res_java", "title": "Junior Java Developer", "status": {"id": "published"}}
            ]
        }
        mock_get.return_value = mock_resp

        best = bot.auto_select_hh_resume("tok")
        self.assertIsNotNone(best)
        self.assertEqual(best["id"], "res_java")

    @patch("bot.send_message")
    @patch("bot.requests.get")
    def test_poll_hh_negotiations_invitation_alert(self, mock_get, mock_send):
        # 1. First run initializes tracker without spam
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.json.return_value = {
            "items": [
                {
                    "id": "neg_100",
                    "state": {"id": "response", "name": "Отклик передан"},
                    "has_updates": False,
                    "vacancy": {"name": "Junior Java", "employer": {"name": "Банк"}},
                    "updated_at": "2026-10-16T10:00:00"
                }
            ]
        }
        mock_get.return_value = mock_resp1
        bot.poll_hh_negotiations_and_notify("tok", 12345, "hh_tok")
        self.assertFalse(mock_send.called)

        # 2. Second run: Employer changes state to invitation -> Alert sent!
        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = {
            "items": [
                {
                    "id": "neg_100",
                    "state": {"id": "invitation", "name": "Приглашение"},
                    "has_updates": True,
                    "vacancy": {"name": "Junior Java", "employer": {"name": "Банк"}},
                    "updated_at": "2026-10-16T11:00:00"
                }
            ]
        }
        mock_get.return_value = mock_resp2
        bot.poll_hh_negotiations_and_notify("tok", 12345, "hh_tok")
        self.assertTrue(mock_send.called)
        sent_text = mock_send.call_args[0][2]
        self.assertIn("ПРИГЛАШЕНИЕ НА СОБЕСЕДОВАНИЕ", sent_text)
        self.assertIn("Банк", sent_text)

    @patch("bot.generate_cover_letter")
    @patch("bot.send_message")
    def test_process_vacancy_quiet_mode_score_6(self, mock_send, mock_letter):
        # Score 6: below notify_min_score (9) and below auto_apply_min_score (8)
        # Should NOT send Telegram notification
        v = {
            "id": "habr_1",
            "title": "Junior Java Developer",
            "company": "Acme",
            "link": "https://career.habr.com/1"
        }
        analysis = {"match_score": 6, "suitable": True, "verdict": "OK", "role": "java"}
        cfg = {"notify_min_score": 9, "auto_apply_enabled": True, "auto_apply_min_score": 8}

        auto_app, notified = bot.process_vacancy_auto_apply_and_notify(
            v, analysis, cfg, "tok", 123, "gkey", "gmodel"
        )
        self.assertFalse(auto_app)
        self.assertFalse(notified)
        self.assertFalse(mock_send.called)
        self.assertFalse(mock_letter.called)

    @patch("bot.generate_cover_letter")
    @patch("bot.send_message")
    def test_process_vacancy_auto_prepared_score_8_no_spam(self, mock_send, mock_letter):
        # Score 8: reaches auto_apply_min_score (8), generates letter,
        # but below notify_min_score (9) -> NO spam in Telegram!
        mock_letter.return_value = "Здравствуйте! Мой стек Java 21..."
        v = {
            "id": "habr_2",
            "title": "Junior Java Developer",
            "company": "Tech Corp",
            "link": "https://career.habr.com/2"
        }
        analysis = {"match_score": 8, "suitable": True, "verdict": "Хороший шанс", "role": "java"}
        cfg = {"notify_min_score": 9, "auto_apply_enabled": True, "auto_apply_min_score": 8}

        auto_app, notified = bot.process_vacancy_auto_apply_and_notify(
            v, analysis, cfg, "tok", 123, "gkey", "gmodel"
        )
        self.assertFalse(auto_app)
        self.assertFalse(notified)
        self.assertFalse(mock_send.called)
        self.assertTrue(mock_letter.called)

        # Check status recorded
        statuses = bot.load_statuses()
        self.assertIn("habr_2", statuses)
        self.assertEqual(statuses["habr_2"]["cover_letter"], "Здравствуйте! Мой стек Java 21...")
        self.assertTrue(statuses["habr_2"]["auto_prepared"])

    @patch("bot.generate_cover_letter")
    @patch("bot.send_message")
    def test_process_vacancy_top_score_9_notifies_with_prepared_letter(self, mock_send, mock_letter):
        # Score 9: Top match! Reaches notify_min_score (9)
        # Sends priority alert with prepared letter and 1-click action buttons
        mock_letter.return_value = "Здравствуйте! Меня заинтересовала вакансия в Tech Corp..."
        v = {
            "id": "habr_3",
            "title": "Junior Java Developer",
            "company": "Tech Corp",
            "link": "https://career.habr.com/3",
            "source_name": "Хабр Карьера"
        }
        analysis = {"match_score": 9, "suitable": True, "verdict": "Топ матч", "role": "java"}
        cfg = {"notify_min_score": 9, "auto_apply_enabled": True, "auto_apply_min_score": 8}

        auto_app, notified = bot.process_vacancy_auto_apply_and_notify(
            v, analysis, cfg, "tok", 123, "gkey", "gmodel"
        )
        self.assertFalse(auto_app)
        self.assertTrue(notified)
        self.assertTrue(mock_send.called)
        sent_text = mock_send.call_args[0][2]
        self.assertIn("ТОП-СОВПАДЕНИЕ: 9/10", sent_text)
        self.assertIn("Tech Corp", sent_text)
        self.assertIn("Авто-подготовленное сопроводительное письмо", sent_text)

    @patch("bot.apply_hh_vacancy_api")
    @patch("bot.generate_cover_letter")
    @patch("bot.send_message")
    def test_process_vacancy_hh_direct_api_auto_apply(self, mock_send, mock_letter, mock_apply_api):
        # HH vacancy with hh_access_token configured -> Direct API submission!
        mock_letter.return_value = "Здравствуйте! Готов к отклику на Java..."
        mock_apply_api.return_value = {"ok": True, "status": "sent", "detail": "Success"}

        v = {
            "id": "hh_555",
            "raw_id": "555",
            "source": "hh",
            "source_name": "HeadHunter",
            "title": "Junior Java Backend",
            "company": "Sber",
            "link": "https://hh.ru/vacancy/555"
        }
        analysis = {"match_score": 9, "suitable": True, "verdict": "Идеально", "role": "java"}
        cfg = {
            "notify_min_score": 9,
            "auto_apply_enabled": True,
            "auto_apply_min_score": 8,
            "hh_access_token": "valid_token",
            "hh_resume_id": "res_java_123"
        }

        auto_app, notified = bot.process_vacancy_auto_apply_and_notify(
            v, analysis, cfg, "tok", 123, "gkey", "gmodel"
        )
        self.assertTrue(auto_app)
        self.assertTrue(notified)
        self.assertTrue(mock_apply_api.called)
        self.assertTrue(mock_send.called)

        # Status check
        statuses = bot.load_statuses()
        self.assertEqual(statuses["hh_555"]["status"], "applied")
        self.assertTrue(statuses["hh_555"]["auto_applied"])
        self.assertEqual(statuses["hh_555"]["method"], "hh_api")

        sent_text = mock_send.call_args[0][2]
        self.assertIn("АВТО-ОТКЛИК ОТПРАВЛЕН! [HeadHunter]", sent_text)
        self.assertIn("Sber", sent_text)

    def test_normalize_vacancy_for_api_includes_cover_letter(self):
        v = {
            "id": "hh_100",
            "title": "Junior Java Developer",
            "company": "Test Co",
            "link": "https://hh.ru/100",
            "description": "Java 21 Spring Boot",
            "match_score": 9,
            "analysis": {"match_score": 9, "suitable": True, "role": "java"}
        }
        statuses = {
            "hh_100": {
                "status": "applied",
                "cover_letter": "My letter",
                "auto_applied": True,
                "applied_at": "2026-10-16T12:00:00"
            }
        }
        res = bot.normalize_vacancy_for_api(v, statuses)
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "applied")
        self.assertEqual(res["cover_letter"], "My letter")
        self.assertTrue(res["auto_applied"])

    @patch("bot.send_message")
    def test_check_and_send_scheduled_digest(self, mock_send):
        cache = {
            "v_1": {
                "id": "v_1",
                "title": "Junior Java Developer",
                "match_score": 7
            }
        }
        with open(bot.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)

        cfg = {"notify_min_score": 9, "daily_digest_enabled": True, "last_digest_time": 0}
        bot.check_and_send_scheduled_digest("tok", 123, cfg)

        self.assertTrue(mock_send.called)
        sent_text = mock_send.call_args[0][2]
        self.assertIn("Сводка Career Hub за 24 часа", sent_text)
        self.assertGreater(cfg["last_digest_time"], 0)

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_api_direct_redirect_303(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 303
        mock_resp.headers = {"Location": "https://company.example.com/apply/123"}
        mock_post.return_value = mock_resp

        res = bot.apply_hh_vacancy_api("138000000", "resume_xyz", "Письмо", "test_tok")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "direct_required")
        self.assertEqual(res.get("redirect_url"), "https://company.example.com/apply/123")

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_api_token_expired(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"error": "bad_authorization", "error_description": "Token is expired"}
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_post.return_value = mock_resp

        res = bot.apply_hh_vacancy_api("138000000", "resume_xyz", "Письмо", "test_tok")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "token_expired")

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_api_limit_exceeded(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"errors": [{"value": "limit_exceeded", "type": "negotiations"}]}
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_post.return_value = mock_resp

        res = bot.apply_hh_vacancy_api("138000000", "resume_xyz", "Письмо", "test_tok")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "limit_exceeded")

    @patch("bot.requests.post")
    def test_send_message_html_fallback_on_parse_error(self, mock_post):
        # 1st call fails with 400 Bad Request: can't parse entities
        err_resp = MagicMock()
        err_resp.status_code = 400
        err_resp.text = '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'
        # 2nd call succeeds without parse_mode
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"ok": True, "result": {"message_id": 999}}
        
        mock_post.side_effect = [err_resp, ok_resp]

        raw_bad_html = "<b>Java Developer</b> <unclosed_custom_tag> & special < chars"
        res = bot.send_message("dummy_token", 12345, raw_bad_html)

        self.assertEqual(mock_post.call_count, 2)
        second_call_payload = mock_post.call_args_list[1][1]["json"]
        self.assertNotIn("parse_mode", second_call_payload)
        self.assertIn("Java Developer", second_call_payload["text"])
        self.assertIsNotNone(res)

    @patch("bot.send_message")
    def test_check_and_send_scheduled_digest_suppressed_when_recent(self, mock_send):
        import time
        cache = {"v_1": {"id": "v_1", "title": "Junior Java", "match_score": 7}}
        with open(bot.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)

        cfg = {"notify_min_score": 9, "daily_digest_enabled": True, "last_digest_time": time.time() - 3600}
        bot.check_and_send_scheduled_digest("tok", 123, cfg)
        self.assertFalse(mock_send.called)

    @patch("bot.send_message")
    def test_check_and_send_scheduled_digest_initializes_on_none(self, mock_send):
        cfg = {"notify_min_score": 9, "daily_digest_enabled": True}
        bot.check_and_send_scheduled_digest("tok", 123, cfg)
        self.assertFalse(mock_send.called)
        self.assertIn("last_digest_time", cfg)
        self.assertGreater(cfg["last_digest_time"], 0)


    @patch("bot.requests.post")
    def test_apply_hh_vacancy_web_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"chat_id": "98765", "topic_id": "123"}
        mock_post.return_value = mock_resp

        cookie = "__ddg1_=xyz; _xsrf=testxsrf; redirect_host=volgograd.hh.ru"
        res = bot.apply_hh_vacancy_web("138255429", "resume_hash_1", "Cover letter", cookie)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "applied")
        self.assertEqual(res["chat_id"], "98765")

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_web_already_applied(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "alreadyApplied"}
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_post.return_value = mock_resp

        cookie = "__ddg1_=xyz; _xsrf=testxsrf; redirect_host=volgograd.hh.ru"
        res = bot.apply_hh_vacancy_web("138255429", "resume_hash_1", "Cover letter", cookie)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "already_applied")

    @patch("bot.requests.post")
    def test_apply_hh_vacancy_web_test_required(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "testRequired"}
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_post.return_value = mock_resp

        cookie = "__ddg1_=xyz; _xsrf=testxsrf; redirect_host=volgograd.hh.ru"
        res = bot.apply_hh_vacancy_web("138255429", "resume_hash_1", "Cover letter", cookie)
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "test_required")

    @patch("bot.requests.get")
    def test_check_hh_web_cookie_valid(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><div data-qa="profile-name">Никита Фролов</div><a href="/resume/b81ca639ff1137b70d0039ed1f57755649766f">Резюме</a></html>'
        mock_get.return_value = mock_resp

        cookie = "__ddg1_=xyz; _xsrf=testxsrf; redirect_host=volgograd.hh.ru"
        prof = bot.check_hh_web_cookie(cookie)
        self.assertIsNotNone(prof)
        self.assertTrue(prof["ok"])
        self.assertEqual(prof["name"], "Никита Фролов")
        self.assertIn("b81ca639ff1137b70d0039ed1f57755649766f", prof["resumes"])

    @patch("bot.requests.post")
    def test_generate_cover_letter_includes_tunnel_and_non_commercial(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Mock network disabled"
        mock_post.return_value = mock_resp
        # Test fallback template
        vacancy = {
            "title": "Junior Java Developer",
            "company": "Sber",
            "description": "Разработка бэкенда на Spring Boot и PostgreSQL",
            "role": "java"
        }
        # In case of API failure / fallback
        letter = bot.generate_cover_letter("dummy_key", "dummy_model", vacancy)
        self.assertIsNotNone(letter)
        self.assertIn("https://demo-resume.trycloudflare.com/", letter)
        self.assertIn("https://park.shadowkek.space", letter)
        self.assertIn("Clean Architecture", letter)
        self.assertIn("Spring Boot 3", letter)
        self.assertIn("Готов выполнить тестовое задание", letter)

    @patch("bot.requests.post")
    def test_generate_cover_letter_fullstack_js_template(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Mock network disabled"
        mock_post.return_value = mock_resp
        vacancy = {
            "title": "Стажёр Fullstack JS",
            "company": "Digital Agency",
            "description": "Ищем стажёра Fullstack JS на React 18 и Node.js",
            "role": "fullstack"
        }
        letter = bot.generate_cover_letter("dummy_key", "dummy_model", vacancy)
        self.assertIsNotNone(letter)
        self.assertIn("https://demo-resume.trycloudflare.com/", letter)
        self.assertIn("https://park.shadowkek.space", letter)
        self.assertIn("https://github.com/nikitos7626/park-client", letter)
        self.assertIn("https://github.com/nikitos7626/Park-server", letter)
        self.assertIn("React 18", letter)
        self.assertIn("Готов выполнить тестовое задание", letter)

    @patch("bot.requests.post")
    def test_generate_cover_letter_groq_appends_tunnel_if_missing(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Здравствуйте! Мой стек Java 21, Spring Boot 3..."}}]
        }
        mock_post.return_value = mock_resp

        vacancy = {"title": "Junior Java Developer", "company": "Tinkoff", "role": "java"}
        letter = bot.generate_cover_letter("test_key", "model", vacancy)
        self.assertIn("https://demo-resume.trycloudflare.com", letter)

    @patch("bot.generate_cover_letter")
    @patch("bot.send_message")
    def test_process_vacancy_score_7_triggers_auto_apply(self, mock_send, mock_letter):
        # Score 7: reaches auto_apply_min_score (7), generates letter,
        # but below notify_min_score (9) -> NO spam in Telegram!
        mock_letter.return_value = "Здравствуйте! Мой стек Java 21... https://demo-resume.trycloudflare.com"
        v = {
            "id": "hh_777",
            "raw_id": "777",
            "source": "hh",
            "title": "Junior Java Developer",
            "company": "Avito",
            "link": "https://hh.ru/vacancy/777"
        }
        analysis = {"match_score": 7, "suitable": True, "verdict": "Хороший матч", "role": "java"}
        cfg = {"notify_min_score": 9, "auto_apply_enabled": True, "auto_apply_min_score": 7}

        auto_app, notified = bot.process_vacancy_auto_apply_and_notify(
            v, analysis, cfg, "tok", 123, "gkey", "gmodel"
        )
        self.assertFalse(auto_app)  # No HH token/cookie set in this test -> auto_prepared
        self.assertFalse(notified)  # score 7 < notify_min_score 9 -> no telegram spam!
        self.assertFalse(mock_send.called)
        self.assertTrue(mock_letter.called)

        # Check status recorded
        statuses = bot.load_statuses()
        self.assertIn("hh_777", statuses)
        self.assertEqual(statuses["hh_777"]["cover_letter"], mock_letter.return_value)
        self.assertTrue(statuses["hh_777"]["auto_prepared"])


    def test_quick_prefilter_accepts_target_roles(self):
        # Junior Java
        self.assertFalse(bot.quick_prefilter_reject("Junior Java Developer", "Spring Boot, REST API, PostgreSQL", "Sber"))
        self.assertFalse(bot.quick_prefilter_reject("Стажер Java разработчик", "Java 21, базы данных", "ПСБ"))
        self.assertFalse(bot.quick_prefilter_reject("Trainee Java Developer", "Java Core, Git", "FinTech"))

        # Junior Fullstack JS / Frontend
        self.assertFalse(bot.quick_prefilter_reject("Junior Fullstack Developer", "React, Node.js, Express, PostgreSQL", "Startup"))
        self.assertFalse(bot.quick_prefilter_reject("Стажер Frontend-разработчик", "HTML, CSS, JavaScript, React", "Tech Co"))
        self.assertFalse(bot.quick_prefilter_reject("Junior JavaScript разработчик", "JS, TypeScript, React, REST API", "Web Studio"))
        self.assertFalse(bot.quick_prefilter_reject("Стажёр фулстек-разработчик", "NodeJS, React, PostgreSQL", "Digital"))

        # Junior Python Backend & AI Engineer
        self.assertFalse(bot.quick_prefilter_reject("Junior Python Developer", "FastAPI, PostgreSQL, REST API", "Tech Solutions"))
        self.assertFalse(bot.quick_prefilter_reject("Стажер Python разработчик", "Python 3, Django, Docker", "FinTech"))
        self.assertFalse(bot.quick_prefilter_reject("AI Engineer (LLM / RAG)", "LangChain, OpenAI API, Python", "AI Labs"))
        self.assertFalse(bot.quick_prefilter_reject("Младший разработчик ИИ", "Нейросети, LLM агенты, API", "GenAI Corp"))

        # Benefits with online course mentions should NOT be rejected
        self.assertFalse(bot.quick_prefilter_reject("Junior Java Developer", "Предоставляем оплату онлайн-курсов и конференций", "Good Co"))

    def test_quick_prefilter_rejects_unwanted(self):
        # Senior / Middle
        self.assertTrue(bot.quick_prefilter_reject("Senior Java Developer", "10+ лет опыта", "Co"))
        self.assertTrue(bot.quick_prefilter_reject("Middle Java Developer", "3+ года опыта", "Co"))
        self.assertTrue(bot.quick_prefilter_reject("Team Lead Java", "управление командой", "Co"))

        # QA / Test
        self.assertTrue(bot.quick_prefilter_reject("QA Automation Engineer", "Selenium, Java", "QA Co"))
        self.assertTrue(bot.quick_prefilter_reject("Инженер по тестированию (Junior)", "Ручное тестирование", "Test Co"))
        self.assertTrue(bot.quick_prefilter_reject("Full Stack Test Engineer Trainee", "Automated and manual testing", "FinTech"))

        # Teaching / Mentoring (strictly rejected)
        self.assertTrue(bot.quick_prefilter_reject("Преподаватель по программированию", "Обучение студентов", "Academy"))
        self.assertTrue(bot.quick_prefilter_reject("Ментор по Python", "Наставничество начинающих", "School"))
        self.assertTrue(bot.quick_prefilter_reject("Учитель информатики и Python", "Ведение занятий", "College"))

        # Blacklist (Aston, Deepra)
        self.assertTrue(bot.quick_prefilter_reject("Junior Java Developer", "Java 21", "Aston"))
        self.assertTrue(bot.quick_prefilter_reject("Junior Java Developer", "Java 21", "Астон"))
        self.assertTrue(bot.quick_prefilter_reject("Junior Python Developer", "Python", "deepra"))

        # Non-IT
        self.assertTrue(bot.quick_prefilter_reject("Менеджер по продажам", "Продажи b2b", "Sales Co"))
        self.assertTrue(bot.quick_prefilter_reject("Бухгалтер", "1C, отчеты", "Finance Co"))

        # Telegram Digests & Compilations
        self.assertTrue(bot.quick_prefilter_reject("Подборка Junior/Стажёр удалённо", "1. React dev\n2. QA", "TG"))
        self.assertTrue(bot.quick_prefilter_reject("Подборка Junior/Стажёр в офис", "Вакансии недели", "TG"))
        self.assertTrue(bot.quick_prefilter_reject("Дайджест вакансий недели", "Подборка предложений", "TG"))

        # 1C
        self.assertTrue(bot.quick_prefilter_reject("Программист 1С (стажер)", "1С:Предприятие", "1C Co"))
        self.assertTrue(bot.quick_prefilter_reject("Стажер-программист 1С", "Доработка конфигураций 1C", "Buh Co"))

        # C# / .NET
        self.assertTrue(bot.quick_prefilter_reject("Стажёр C#-разработчик, Ozon Tech", "C#, .NET Core", "Ozon"))
        self.assertTrue(bot.quick_prefilter_reject("Junior .NET Developer", "ASP.NET Core, C#", "FinTech"))

        # Go / Golang
        self.assertTrue(bot.quick_prefilter_reject("Go-разработчик стажер", "Golang, микросервисы", "Delivery"))
        self.assertTrue(bot.quick_prefilter_reject("Стажёр Go-разработчик, Логистика", "Go, gRPC", "Logistics"))

        # GameDev & 3D
        self.assertTrue(bot.quick_prefilter_reject("Junior Unity Developer на мобильную RTS", "Unity, C#, mobile", "Games"))
        self.assertTrue(bot.quick_prefilter_reject("Trainee Unreal Engine Developer", "Unreal, Blueprints", "GameLab"))

        # Mobile (iOS, Swift, Flutter, generic mobile without web)
        self.assertTrue(bot.quick_prefilter_reject("Стажер по мобильной разработке (iOS)", "Swift, UIKit", "App Studio"))
        self.assertTrue(bot.quick_prefilter_reject("Младший разработчик мобильных решений", "Android, Kotlin SDK", "Mobility"))

        # QA / Test
        self.assertTrue(bot.quick_prefilter_reject("Full Stack Test Engineer Trainee", "Automated and manual testing", "FinTech"))

        # Teaching / Mentoring (strictly unwanted)
        self.assertTrue(bot.quick_prefilter_reject("Преподаватель по программированию", "Обучение студентов", "Academy"))
        self.assertTrue(bot.quick_prefilter_reject("Ментор по Python", "Наставничество начинающих", "School"))

        # PHP / C++
        self.assertTrue(bot.quick_prefilter_reject("Junior PHP Developer", "Laravel, MySQL", "WebShop"))
        self.assertTrue(bot.quick_prefilter_reject("Junior C++ Developer", "STL, multithreading", "Soft"))

        # Analytics / DevOps
        self.assertTrue(bot.quick_prefilter_reject("Data Scientist (Junior)", "Pandas, ML", "AI Lab"))
        self.assertTrue(bot.quick_prefilter_reject("Junior DevOps Engineer", "Docker, Kubernetes, CI/CD", "Cloud"))

    @patch("bot.requests.post")
    def test_generate_cover_letter_includes_both_links(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Mock network disabled"
        mock_post.return_value = mock_resp
        # Fullstack JS fallback letter
        v_fs = {"title": "Junior Fullstack Developer", "company": "Acme", "role": "fullstack_js"}
        letter_fs = bot.generate_cover_letter("dummy_key", "invalid_model", v_fs)
        self.assertIn("https://demo-resume.trycloudflare.com", letter_fs)
        self.assertIn("https://park.shadowkek.space", letter_fs)
        self.assertIn("https://github.com/nikitos7626/park-client", letter_fs)
        self.assertIn("https://github.com/nikitos7626/Park-server", letter_fs)

        # Java fallback letter
        v_java = {"title": "Junior Java Developer", "company": "Acme", "role": "java"}
        letter_java = bot.generate_cover_letter("dummy_key", "invalid_model", v_java)
        self.assertIn("https://demo-resume.trycloudflare.com", letter_java)
        self.assertIn("https://park.shadowkek.space", letter_java)
        self.assertIn("https://github.com/nikitos7626/BackendSpringBlog", letter_java)

        # AI & LLM fallback letter with pet project
        v_ai = {"title": "AI Engineer (LLM / RAG)", "company": "NeuroTech", "role": "ai_dev"}
        letter_ai = bot.generate_cover_letter("dummy_key", "invalid_model", v_ai)
        self.assertIn("https://demo-resume.trycloudflare.com", letter_ai)
        self.assertIn("https://park.shadowkek.space", letter_ai)
        self.assertIn("https://github.com/nikitos7626/mvideo-nlp", letter_ai)
        self.assertIn("Готов выполнить тестовое задание", letter_ai)

        # Python Backend fallback letter with pet projects
        v_py = {"title": "Junior Python Backend Developer", "company": "PyStudio", "role": "python_dev"}
        letter_py = bot.generate_cover_letter("dummy_key", "invalid_model", v_py)
        self.assertIn("https://demo-resume.trycloudflare.com", letter_py)
        self.assertIn("https://park.shadowkek.space", letter_py)
        self.assertIn("https://github.com/nikitos7626/mvideo-nlp", letter_py)
        self.assertIn("https://github.com/nikitos7626/ExCalendar", letter_py)
        self.assertIn("Готов выполнить тестовое задание", letter_py)

    def test_quick_prefilter_target_vs_unwanted_comprehensive(self):
        # Target roles that MUST pass (return False from quick_prefilter_reject)
        targets = [
            ("Junior Java Developer", "Spring Boot, REST API, PostgreSQL", "Sber"),
            ("Стажер Java разработчик", "Java 21, базы данных", "T-Bank"),
            ("Trainee Java Developer", "Java Core, Git", "FinTech"),
            ("Junior Fullstack Developer", "React, Node.js, Express, PostgreSQL", "Startup"),
            ("Стажер Frontend-разработчик", "HTML, CSS, JavaScript, React", "Tech Co"),
            ("Junior JavaScript разработчик", "JS, TypeScript, React, REST API", "Web Studio"),
            ("Стажёр фулстек-разработчик", "NodeJS, React, PostgreSQL", "Digital"),
            ("Стажер разработчик AI-агентов", "Spring AI, LangChain4j, Java", "AI Lab"),
            ("AI Engineer", "LLM, RAG, LangChain, Python", "AI Labs"),
            ("Junior Python Developer", "Django, FastAPI", "PyWeb"),
            ("Python-разработчик (Junior+)", "FastAPI, REST API", "WebStudio"),
            ("Разработчик VPN сервисов", "Xray, Sing-box, Remnawave", "Network Co"),
        ]
        for title, desc, comp in targets:
            self.assertFalse(bot.quick_prefilter_reject(title, desc, comp), f"Target role incorrectly rejected: {title}")

        # Unwanted roles that MUST be rejected (return True from quick_prefilter_reject)
        unwanted = [
            ("Junior Unity Developer на мобильную RTS", "Unity, C#", "GameDev"),
            ("Стажёр C#-разработчик, Ozon Tech", "C#, .NET Core", "Ozon"),
            ("Go-разработчик стажер", "Golang, микросервисы", "Delivery"),
            ("Стажёр Go-разработчик, Логистика", "Go, gRPC", "Logistics"),
            ("Программист 1С (стажер)", "1C:Предприятие", "1C Studio"),
            ("Стажер-программист 1С", "1C ERP", "Retail"),
            ("Стажер по мобильной разработке (iOS)", "Swift, UIKit", "MobileApp"),
            ("Младший разработчик мобильных решений", "Android Kotlin SDK", "Solutions"),
            ("Подборка Junior/Стажёр удалённо", "1. QA 2. Design", "Channel"),
            ("Подборка Junior/Стажёр в офис", "Вакансии дня", "Channel"),
            ("Дайджест вакансий недели", "Подборка", "Channel"),
            ("Junior DevOps Engineer", "Kubernetes, Docker", "Ops"),
            ("Data Scientist (Junior)", "PyTorch, Pandas", "Data Co"),
            ("Преподаватель Python", "Курсы для детей", "OnlineSchool"),
            ("Наставник на курс по разработке", "Проверка домашних заданий", "Practicum"),
            ("QA Automation Engineer", "Pytest, Selenium", "QA Labs"),
            ("Full Stack Test Engineer Trainee", "Test automation", "Tech QA"),
            ("Junior PHP Developer", "Laravel, MySQL", "Web"),
            ("Junior C++ Developer", "C++, STL, memory", "HardSoft"),
        ]
        for title, desc, comp in unwanted:
            self.assertTrue(bot.quick_prefilter_reject(title, desc, comp), f"Unwanted role NOT rejected: {title}")

    @patch("bot.requests.post")
    def test_delete_hh_negotiation_web_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "{}"
        mock_post.return_value = mock_resp

        cookie = "_xsrf=test_xsrf_token; redirect_host=volgograd.hh.ru"
        res = bot.delete_hh_negotiation_web("topic_555", cookie)
        self.assertTrue(res["ok"])
        self.assertIn("успешно удален", res["detail"])
        # Verify request parameters
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        self.assertEqual(call_url, "https://volgograd.hh.ru/applicant/negotiations/trash")
        self.assertEqual(mock_post.call_args[1]["data"]["topic"], "topic_555")
        self.assertEqual(mock_post.call_args[1]["data"]["substate"], "HIDE")

    def test_remove_vacancy_from_profile_and_history(self):
        v_id = "hh_888999"
        # Seed into statuses and cache
        bot.update_vacancy_status(v_id, "applied", cover_letter="Letter")
        bot.save_to_cache({"id": v_id, "title": "Test Vacancy", "match_score": 8})

        # Confirm existence
        self.assertIn(v_id, bot.load_statuses())
        self.assertIn(v_id, bot.load_cache())

        # Call remove
        bot.remove_vacancy_from_profile_and_history(v_id)

        # Confirm removal
        self.assertNotIn(v_id, bot.load_statuses())
        self.assertNotIn(v_id, bot.load_cache())

    @patch("bot.delete_hh_negotiation_web")
    @patch("bot.remove_vacancy_from_profile_and_history")
    @patch("bot.send_message")
    @patch("bot.requests.get")
    def test_poll_hh_negotiations_auto_deletes_discards(self, mock_get, mock_send, mock_remove, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Simulated Redux InitialState containing 1 discard topic
        initial_state = {
            "applicantNegotiations": {
                "topicList": [
                    {
                        "id": 999111,
                        "vacancyId": 777222,
                        "lastState": "DISCARD",
                        "viewedByOpponent": True
                    }
                ]
            },
            "vacanciesShort": {
                "vacanciesList": [
                    {
                        "vacancyId": 777222,
                        "name": "Junior AI Developer",
                        "employer": {"name": "Test Company"},
                        "links": {"desktop": "https://hh.ru/vacancy/777222"}
                    }
                ]
            }
        }
        mock_resp.text = f'<html><template id="HH-Lux-InitialState">{json.dumps(initial_state)}</template></html>'
        mock_get.return_value = mock_resp

        cookie = "_xsrf=test_xsrf; redirect_host=volgograd.hh.ru"
        bot.poll_hh_negotiations_and_notify("bot_tok", 12345, cookie_str=cookie)

        # Verify delete_hh_negotiation_web was called with topic_id
        mock_delete.assert_called_once_with("999111", cookie, "volgograd.hh.ru")
        # Verify remove_vacancy_from_profile_and_history was called
        mock_remove.assert_called_once_with("hh_777222")
        # Verify user was notified in Telegram
        self.assertTrue(mock_send.called)
        sent_msg = mock_send.call_args[0][2]
        self.assertIn("Отказ автоматически удален", sent_msg)
        self.assertIn("Junior AI Developer", sent_msg)

if __name__ == "__main__":
    unittest.main()

