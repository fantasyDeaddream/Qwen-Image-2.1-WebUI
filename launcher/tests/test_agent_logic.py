"""Offline unit tests for launcher/agent.py -- no network, no Qt, no API key.

Everything the frozen launcher depends on for correctness lives here: the model
classification (which names are not chat models at all), the three-way vision
probe, the JSON contract the agent must fulfil, and the clamping that stops a
creative model from producing an impossible draw plan.

    cd Qwen-Image-2.1-WebUI\\launcher
    ..\\.venv\\Scripts\\python.exe tests\\test_agent_logic.py

Exit code 0 means all passed.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # launcher dir

import agent                                                    # noqa: E402

MAX_PIXELS = 4194304                                            # 2048 * 2048


def warns(plan, needle: str) -> bool:
    return any(needle in w for w in plan.warnings)


# ---------------------------------------------------------------------------
#  model classification: only evidence may hide a model
# ---------------------------------------------------------------------------
class TestClassification(unittest.TestCase):
    def test_not_chat_models_are_excluded(self):
        for name in ("glm-ocr", "embedding-3", "bge-rerank", "tts-1",
                     "whisper-1", "cogview-4", "glm-image", "codegeex-4",
                     "stable-diffusion-xl", "glm-realtime", "text-moderation-latest"):
            ok, why = agent.is_conversational(name)
            self.assertFalse(ok, f"{name} 应该被排除")
            self.assertTrue(why, f"{name} 的排除理由不能为空")

    def test_chat_models_are_kept_even_without_a_vision_hint(self):
        # deepseek-flash / glm-5.3-flash read images although the name has no "v"
        for name in ("deepseek-chat", "deepseek-flash", "glm-4-plus",
                     "glm-5.3-flash", "glm-4.6v", "glm-5v-turbo"):
            self.assertTrue(agent.is_conversational(name)[0], name)

    def test_empty_name_is_not_conversational(self):
        self.assertFalse(agent.is_conversational("")[0])
        self.assertFalse(agent.is_conversational("   ")[0])
        self.assertFalse(agent.is_conversational(None)[0])

    def test_looks_multimodal_is_only_a_hint(self):
        self.assertTrue(agent.looks_multimodal("glm-4.6v"))
        self.assertTrue(agent.looks_multimodal("qwen-vl-max"))
        self.assertFalse(agent.looks_multimodal("deepseek-flash"))
        # the point: no-hint must NOT mean excluded
        self.assertTrue(agent.is_conversational("deepseek-flash")[0])

    def test_filter_vision_never_hides_anything(self):
        yes, no, bad = agent.filter_vision(
            ["glm-4.6v", "deepseek-flash", "glm-ocr", "embedding-2"])
        self.assertIn("glm-4.6v", yes)
        self.assertIn("deepseek-flash", no)              # guess bucket, not hidden
        self.assertEqual([m for m, _w in bad], ["glm-ocr", "embedding-2"])
        self.assertEqual(len(yes) + len(no) + len(bad), 4)


# ---------------------------------------------------------------------------
#  GET /models parsing
# ---------------------------------------------------------------------------
class TestListModels(unittest.TestCase):
    def _with_get(self, payload):
        with mock.patch.object(agent, "_get", return_value=payload) as got:
            out = agent.list_models("glm", "sk-test")
        return out, got

    def test_openai_shape_data_list_of_dicts(self):
        out, got = self._with_get({"object": "list", "data": [
            {"id": "glm-4.6v"}, {"id": "glm-4-plus"}]})
        self.assertEqual(out, ["glm-4-plus", "glm-4.6v"])    # sorted, deduped
        url, _key = got.call_args[0]
        self.assertEqual(url, "https://open.bigmodel.cn/api/paas/v4/models")

    def test_alternate_keys_and_plain_strings(self):
        self.assertEqual(self._with_get({"models": ["b", "a"]})[0], ["a", "b"])
        self.assertEqual(self._with_get({"result": [{"name": "c"}]})[0], ["c"])

    def test_bare_list_response(self):
        self.assertEqual(self._with_get([{"model": "m2"}, "m1"])[0], ["m1", "m2"])

    def test_garbage_entries_are_dropped(self):
        out, _ = self._with_get({"data": [
            {"id": "ok-1"}, {"id": ""}, {"id": None}, {"nope": 1}, 42]})
        self.assertEqual(out, ["ok-1"])

    def test_unknown_shape_is_empty_not_an_exception(self):
        self.assertEqual(self._with_get({"weird": True})[0], [])
        self.assertEqual(self._with_get("nonsense")[0], [])

    def test_deepseek_base_url(self):
        with mock.patch.object(agent, "_get", return_value={"data": [{"id": "deepseek-chat"}]}) as got:
            agent.list_models("deepseek", "sk-test")
        self.assertEqual(got.call_args[0][0], "https://api.deepseek.com/models")


# ---------------------------------------------------------------------------
#  capability probe: vision / text / unknown
# ---------------------------------------------------------------------------
GLM_TEXT_ERROR = agent.AgentError(
    "HTTP 400：{\"error\":{\"code\":\"1210\",\"message\":"
    "\"messages.content.type 参数非法，取值范围 ['text']\"}}")


def _probe_raising(exc):
    with mock.patch.object(agent, "_post", side_effect=exc):
        return agent.probe_vision("glm", "sk-test", "glm-4-flash")


class TestProbeVision(unittest.TestCase):
    def test_accepting_image_means_vision(self):
        with mock.patch.object(agent, "_post", return_value={"choices": []}) as post:
            tier, why = agent.probe_vision("glm", "sk-test", "glm-4.6v")
        self.assertEqual(tier, "vision")
        self.assertIn("接受图片", why)
        payload = post.call_args[0][2]
        self.assertEqual(payload["max_tokens"], 1)
        kinds = [c["type"] for c in payload["messages"][0]["content"]]
        self.assertEqual(kinds, ["text", "image_url"])

    def test_glm_1210_rejection_proves_text_only(self):
        tier, why = _probe_raising(GLM_TEXT_ERROR)
        self.assertEqual(tier, "text")
        self.assertIn("text", why)

    def test_any_content_type_1210_is_text_only(self):
        tier, _ = _probe_raising(agent.AgentError(
            "HTTP 400：{\"error\":{\"code\":\"1210\",\"message\":\"content.type 非法\"}}"))
        self.assertEqual(tier, "text")

    def test_missing_model_is_unknown_not_text(self):
        for msg in ("HTTP 404：model not found",
                    "HTTP 400：{\"error\":{\"code\":\"1220\",\"message\":\"模型不存在\"}}",
                    "HTTP 429：rate limit reached (quota)",
                    "URLError: timed out"):
            tier, _ = _probe_raising(agent.AgentError(msg))
            self.assertEqual(tier, "unknown", msg)

    def test_unknown_is_never_evidence_of_absence(self):
        """The whole design rule: only 'text' may hide a model."""
        tiers = {_probe_raising(agent.AgentError(m))[0] for m in
                 ("HTTP 500：boom", "socket timeout", "HTTP 403：forbidden")}
        self.assertEqual(tiers, {"unknown"})

    def test_glm_image_url_is_bare_base64(self):
        with mock.patch.object(agent, "_post", return_value={}) as post:
            agent.probe_vision("glm", "sk-test", "glm-4.6v")
        url = post.call_args[0][2]["messages"][0]["content"][1]["image_url"]["url"]
        self.assertFalse(url.startswith("data:"), "GLM 要裸 base64")

    def test_deepseek_image_url_is_a_data_url(self):
        with mock.patch.object(agent, "_post", return_value={}) as post:
            agent.probe_vision("deepseek", "sk-test", "deepseek-flash")
        url = post.call_args[0][2]["messages"][0]["content"][1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"), url[:40])


# ---------------------------------------------------------------------------
#  refresh(): list, probe, and keep the provider's own words
# ---------------------------------------------------------------------------
class TestRefresh(unittest.TestCase):
    LISTED = ["glm-4-flash", "glm-4.6v", "glm-4-plus", "glm-ocr", "embedding-3"]

    def test_three_buckets_and_extras(self):
        seen = []
        VISION = {"glm-4.6v", "glm-5v-turbo", "glm-4v-flash"}

        def fake_probe(pkey, key, model, timeout=45):
            seen.append(model)
            if model in VISION:
                return ("vision", "实测：接受图片输入")
            if model == "glm-4-flash":
                return ("text", "实测：只接受 text")
            if model == "glm-4-plus":
                return ("unknown", "无法判定：quota")
            return ("unknown", "无法判定")

        with mock.patch.object(agent, "list_models", return_value=self.LISTED), \
             mock.patch.object(agent, "probe_vision", side_effect=fake_probe):
            res = agent.refresh("glm", "sk-test")

        self.assertEqual(res["listed"], self.LISTED)
        self.assertEqual(sorted(res["vision"]), sorted(VISION))
        self.assertEqual([m for m, _w in res["text_only"]], ["glm-4-flash"])
        unknown = [m for m, _w in res["unknown"]]
        self.assertIn("glm-4-plus", unknown)          # no verdict -> still selectable
        # non-conversational names are excluded without spending a probe
        self.assertEqual([m for m, _w in res["excluded"]], ["glm-ocr", "embedding-3"])
        self.assertNotIn("glm-ocr", seen)
        self.assertNotIn("embedding-3", seen)
        # documented vision models missing from GET /models are still probed
        self.assertIn("glm-5v-turbo", res["extras_tried"])
        self.assertIn("glm-5v-turbo", seen)
        # every candidate got exactly one probe
        self.assertEqual(len(seen), len(set(seen)), "不应重复探测")

    def test_progress_is_reported(self):
        said = []
        with mock.patch.object(agent, "list_models", return_value=["glm-4-plus"]), \
             mock.patch.object(agent, "probe_vision", return_value=("unknown", "?")):
            agent.refresh("glm", "sk-test", on_progress=said.append)
        self.assertTrue(any("GET /models" in s for s in said), said)
        self.assertTrue(any("glm-4-plus" in s for s in said), said)

    def test_deepseek_has_no_documented_extras(self):
        with mock.patch.object(agent, "list_models", return_value=["deepseek-chat"]), \
             mock.patch.object(agent, "probe_vision", return_value=("vision", "ok")):
            res = agent.refresh("deepseek", "sk-test")
        self.assertEqual(res["extras_tried"], [])
        self.assertEqual(res["vision"], ["deepseek-chat"])


# ---------------------------------------------------------------------------
#  request building
# ---------------------------------------------------------------------------
class TestMessages(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\nfake"

    def test_structure_and_order(self):
        msgs = agent.build_messages(agent.PROVIDERS["glm"], "画一只猫",
                                    [("a.png", self.PNG), ("b.png", self.PNG)], 3, True)
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertIn("Qwen-Image-2.1", msgs[0]["content"])
        content = msgs[1]["content"]
        self.assertIn("画一只猫", content[0]["text"])
        self.assertIn("count=3", content[0]["text"])
        self.assertIn("transparent=true", content[0]["text"])
        self.assertIn("always wins", content[0]["text"])
        labels = [c["text"] for c in content if c["type"] == "text"][1:]
        self.assertEqual(labels, ["[picture 0]", "[picture 1]"])
        self.assertEqual([c["type"] for c in content],
                         ["text", "text", "image_url", "text", "image_url"])

    def test_empty_prompt_still_sends_the_pictures(self):
        msgs = agent.build_messages(agent.PROVIDERS["glm"], "   ", [("a.png", self.PNG)], 1)
        self.assertIn("only attached pictures", msgs[1]["content"][0]["text"])

    def test_glm_vs_deepseek_image_encoding(self):
        glm = agent.build_messages(agent.PROVIDERS["glm"], "x", [("a.png", self.PNG)], 1)
        ds = agent.build_messages(agent.PROVIDERS["deepseek"], "x", [("a.png", self.PNG)], 1)
        self.assertFalse(glm[1]["content"][2]["image_url"]["url"].startswith("data:"))
        self.assertTrue(ds[1]["content"][2]["image_url"]["url"].startswith(
            "data:image/png;base64,"))


# ---------------------------------------------------------------------------
#  ask(): one round trip, two attempts at most
# ---------------------------------------------------------------------------
class TestAsk(unittest.TestCase):
    def _reply(self, text):
        return {"choices": [{"message": {"content": text}}]}

    def test_returns_dict(self):
        with mock.patch.object(agent, "_post", return_value=self._reply('{"prompt": "a cat"}')):
            data = agent.ask("glm", "sk-test", "glm-4.6v", "猫", [], 1)
        self.assertEqual(data["prompt"], "a cat")

    def test_response_format_retry_once_without_it(self):
        calls = []

        def post(url, key, payload, timeout=agent.TIMEOUT):
            calls.append(dict(payload))
            if "response_format" in payload:
                raise agent.AgentError('HTTP 400：unknown field "response_format"')
            return self._reply('{"prompt": "ok"}')

        with mock.patch.object(agent, "_post", side_effect=post):
            data = agent.ask("glm", "sk-test", "glm-4.6v", "猫", [], 1)
        self.assertEqual(data["prompt"], "ok")
        self.assertEqual(len(calls), 2)
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])

    def test_hard_errors_are_not_retried(self):
        with mock.patch.object(agent, "_post", side_effect=agent.AgentError("HTTP 429：quota")):
            with self.assertRaises(agent.AgentError):
                agent.ask("glm", "sk-test", "glm-4.6v", "猫", [], 1)

    def test_broken_response_shape_raises(self):
        with mock.patch.object(agent, "_post", return_value={"choices": []}):
            with self.assertRaises(agent.AgentError):
                agent.ask("glm", "sk-test", "glm-4.6v", "猫", [], 1)


# ---------------------------------------------------------------------------
#  _extract_json(): models wrap their JSON in prose and fences
# ---------------------------------------------------------------------------
class TestExtractJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(agent._extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(agent._extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(agent._extract_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_prose_around_it(self):
        text = 'Sure! Here is the plan:\n{"a": {"b": 2}, "c": [1, 2]}\nHope it helps.'
        self.assertEqual(agent._extract_json(text), {"a": {"b": 2}, "c": [1, 2]})

    def test_braces_inside_strings_do_not_confuse_it(self):
        text = '{"prompt": "a {curly} cat", "count": 2}'
        self.assertEqual(agent._extract_json(text)["prompt"], "a {curly} cat")

    def test_no_json_raises(self):
        for text in ("", "I cannot help with that.", '{"broken": '):
            with self.assertRaises(agent.AgentError, msg=text):
                agent._extract_json(text)


# ---------------------------------------------------------------------------
#  Plan.from_agent(): the contract, validated and clamped
# ---------------------------------------------------------------------------
FULL = {"mode": "edit", "prompt": "a red cube", "negative_prompt": "blurry",
        "width": 1024, "height": 768, "transparent": True, "latent_mode": "reference",
        "count": 2, "edit_image_index": 1, "reference_indices": [0, 1],
        "reasoning": "用户要改第二张图"}


class TestPlanBasics(unittest.TestCase):
    def test_valid_plan_passes_through_untouched(self):
        p = agent.Plan.from_agent(dict(FULL), 2, MAX_PIXELS)
        self.assertEqual(p.mode, "edit")
        self.assertEqual(p.prompt, "a red cube")
        self.assertEqual(p.negative_prompt, "blurry")
        self.assertEqual((p.width, p.height), (1024, 768))
        self.assertTrue(p.transparent)
        self.assertEqual(p.latent_mode, "reference")
        self.assertEqual(p.count, 2)
        self.assertEqual(p.edit_image_index, 1)
        self.assertEqual(p.reference_indices, [0, 1])
        self.assertEqual(p.reasoning, "用户要改第二张图")
        self.assertEqual(p.warnings, [])

    def test_empty_object_is_a_safe_1024_text2image(self):
        p = agent.Plan.from_agent({}, 0, MAX_PIXELS)
        self.assertEqual((p.mode, p.width, p.height, p.count), ("text2image", 1024, 1024, 1))
        self.assertTrue(warns(p, "提示词"), "缺提示词必须提醒")

    def test_unknown_mode_falls_back_with_a_warning(self):
        p = agent.Plan.from_agent({"mode": "video"}, 0, MAX_PIXELS)
        self.assertEqual(p.mode, "text2image")
        self.assertTrue(warns(p, "video"))

    def test_summary_shows_every_warning(self):
        p = agent.Plan.from_agent({"mode": "video", "prompt": "x", "count": 99},
                                  0, MAX_PIXELS)
        text = p.summary()
        for w in p.warnings:
            self.assertIn("⚠", text)
            self.assertIn(w, text)


class TestPlanSize(unittest.TestCase):
    def _size(self, w, h):
        p = agent.Plan.from_agent({"prompt": "x", "width": w, "height": h}, 0, MAX_PIXELS)
        return p.width, p.height, p

    def test_aligned_to_32(self):
        for w, h in ((1000, 768), (513, 999), (1024, 1024), (768, 512)):
            pw, ph, _p = self._size(w, h)
            self.assertEqual(pw % 32, 0, (w, h))
            self.assertEqual(ph % 32, 0, (w, h))

    def test_alignment_is_announced(self):
        _w, _h, p = self._size(1000, 1000)
        self.assertTrue(any("32" in w for w in p.warnings), p.warnings)

    def test_minimum_side(self):
        pw, ph, _p = self._size(1, 1)
        self.assertGreaterEqual(min(pw, ph), 256)

    def test_pixel_cap(self):
        for w, h in ((4096, 4096), (3000, 3000), (2048, 4096)):
            pw, ph, p = self._size(w, h)
            self.assertLessEqual(pw * ph, MAX_PIXELS, (w, h))
            self.assertEqual(pw % 32, 0)
            self.assertEqual(ph % 32, 0)
            self.assertTrue(any("上限" in x for x in p.warnings), p.warnings)

    def test_2048_square_is_allowed(self):
        pw, ph, p = self._size(2048, 2048)
        self.assertEqual((pw, ph), (2048, 2048))
        self.assertFalse(any("上限" in x for x in p.warnings), p.warnings)

    def test_non_numeric_size_is_warned_and_defaulted(self):
        p = agent.Plan.from_agent({"prompt": "x", "width": "wide", "height": None},
                                  0, MAX_PIXELS)
        self.assertEqual((p.width, p.height), (1024, 1024))
        self.assertTrue(warns(p, "width"))
        self.assertTrue(warns(p, "height"))


class TestPlanCount(unittest.TestCase):
    def _count(self, value):
        return agent.Plan.from_agent({"prompt": "x", "count": value}, 0, MAX_PIXELS)

    def test_clamped_to_1_8(self):
        self.assertEqual(self._count(0).count, 1)
        self.assertEqual(self._count(-3).count, 1)
        self.assertEqual(self._count(99).count, 8)
        self.assertEqual(self._count(8).count, 8)

    def test_clamping_is_announced(self):
        p = self._count(99)
        self.assertTrue(warns(p, "张数") or warns(p, "count"), p.warnings)

    def test_numeric_strings_are_accepted_without_a_warning(self):
        p = self._count("3")
        self.assertEqual(p.count, 3)
        self.assertEqual(p.warnings, [])

    def test_float_string_is_truncated(self):
        self.assertEqual(self._count("2.0").count, 2)

    def test_garbage_falls_back_to_one_and_warns(self):
        p = self._count("many")
        self.assertEqual(p.count, 1)
        self.assertTrue(warns(p, "count"))


class TestPlanImages(unittest.TestCase):
    def test_edit_index_out_of_range_falls_back_to_zero(self):
        p = agent.Plan.from_agent({"mode": "edit", "prompt": "x", "edit_image_index": 7},
                                  2, MAX_PIXELS)
        self.assertEqual(p.mode, "edit")
        self.assertEqual(p.edit_image_index, 0)
        self.assertTrue(warns(p, "越界"))

    def test_edit_index_in_range_is_kept(self):
        p = agent.Plan.from_agent({"mode": "edit", "prompt": "x", "edit_image_index": 2},
                                  3, MAX_PIXELS)
        self.assertEqual(p.edit_image_index, 2)

    def test_reference_indices_filtered(self):
        p = agent.Plan.from_agent(
            {"mode": "reference", "prompt": "x", "reference_indices": [0, 2, 9, -1]},
            3, MAX_PIXELS)
        self.assertEqual(p.reference_indices, [0, 2])
        self.assertTrue(warns(p, "参考图索引"), "过滤掉越界索引要告诉用户")

    def test_reference_mode_without_valid_index_uses_the_first(self):
        p = agent.Plan.from_agent(
            {"mode": "reference", "prompt": "x", "reference_indices": [9]}, 3, MAX_PIXELS)
        self.assertEqual(p.reference_indices, [0])
        self.assertTrue(warns(p, "参考图"))

    def test_non_integer_indices_are_dropped_with_a_warning(self):
        p = agent.Plan.from_agent(
            {"mode": "reference", "prompt": "x", "reference_indices": ["a", 1]},
            3, MAX_PIXELS)
        self.assertEqual(p.reference_indices, [1])
        self.assertTrue(warns(p, "不是整数"))

    def test_single_int_reference_is_wrapped(self):
        p = agent.Plan.from_agent(
            {"mode": "reference", "prompt": "x", "reference_indices": 1}, 3, MAX_PIXELS)
        self.assertEqual(p.reference_indices, [1])

    def test_no_pictures_means_text2image_and_custom_latent(self):
        p = agent.Plan.from_agent({"mode": "edit", "prompt": "x", "latent_mode": "reference"},
                                  0, MAX_PIXELS)
        self.assertEqual(p.mode, "text2image")
        self.assertEqual(p.latent_mode, "custom")
        self.assertEqual(p.edit_image_index, -1)
        self.assertEqual(p.reference_indices, [])
        self.assertTrue(warns(p, "text2image"))

    def test_latent_mode_only_ever_reference_or_custom(self):
        for value in ("reference", "REFERENCE", "custom", "banana"):
            p = agent.Plan.from_agent({"prompt": "x", "latent_mode": value}, 1, MAX_PIXELS)
            self.assertIn(p.latent_mode, ("reference", "custom"))


class TestPlanTransparent(unittest.TestCase):
    def test_real_booleans(self):
        self.assertTrue(agent.Plan.from_agent({"prompt": "x", "transparent": True},
                                              0, MAX_PIXELS).transparent)
        self.assertFalse(agent.Plan.from_agent({"prompt": "x", "transparent": False},
                                               0, MAX_PIXELS).transparent)

    def test_string_booleans_are_understood(self):
        self.assertTrue(agent.Plan.from_agent({"prompt": "x", "transparent": "true"},
                                              0, MAX_PIXELS).transparent)
        for value in ("false", "no", "0", ""):
            self.assertFalse(agent.Plan.from_agent(
                {"prompt": "x", "transparent": value}, 0, MAX_PIXELS).transparent, value)

    def test_unparseable_value_warns(self):
        p = agent.Plan.from_agent({"prompt": "x", "transparent": "maybe"}, 0, MAX_PIXELS)
        self.assertFalse(p.transparent)
        self.assertTrue(warns(p, "transparent"))


# ---------------------------------------------------------------------------
#  Plan.ordered_images(): the edited picture must be first
# ---------------------------------------------------------------------------
class TestOrderedImages(unittest.TestCase):
    IMGS = ["img0", "img1", "img2"]

    def test_edit_puts_the_edited_picture_first(self):
        p = agent.Plan(mode="edit", edit_image_index=2)
        self.assertEqual(p.ordered_images(self.IMGS), ["img2", "img0", "img1"])

    def test_reference_puts_the_picked_pictures_first(self):
        p = agent.Plan(mode="reference", reference_indices=[2, 0])
        self.assertEqual(p.ordered_images(self.IMGS), ["img2", "img0", "img1"])

    def test_text2image_keeps_the_given_order(self):
        p = agent.Plan(mode="text2image")
        self.assertEqual(p.ordered_images(self.IMGS), self.IMGS)

    def test_no_pictures(self):
        self.assertEqual(agent.Plan(mode="edit", edit_image_index=0).ordered_images([]), [])

    def test_out_of_range_edit_index_does_not_explode(self):
        p = agent.Plan(mode="edit", edit_image_index=5)
        self.assertEqual(len(p.ordered_images(self.IMGS)), 3)


# ---------------------------------------------------------------------------
#  the provider table itself
# ---------------------------------------------------------------------------
class TestProviders(unittest.TestCase):
    def test_two_providers_match_the_documented_endpoints(self):
        self.assertEqual(set(agent.PROVIDERS), {"deepseek", "glm"})
        self.assertEqual(agent.PROVIDERS["deepseek"].base, "https://api.deepseek.com")
        self.assertEqual(agent.PROVIDERS["glm"].base,
                         "https://open.bigmodel.cn/api/paas/v4")
        self.assertTrue(agent.PROVIDERS["glm"].bare_base64)
        self.assertFalse(agent.PROVIDERS["deepseek"].bare_base64)
        for p in agent.PROVIDERS.values():
            self.assertTrue(p.signup.startswith("https://"))
            self.assertEqual(p.name, p.name.strip())

    def test_system_prompt_documents_every_contract_field(self):
        for key in ("mode", "prompt", "negative_prompt", "width", "height",
                    "transparent", "latent_mode", "count", "edit_image_index",
                    "reference_indices", "reasoning"):
            self.assertIn(key, agent.SYSTEM_PROMPT, key)
        for mode in agent.MODES:
            self.assertIn(mode, agent.SYSTEM_PROMPT)

    def test_no_api_key_is_hardcoded_anywhere(self):
        import re
        src = Path(agent.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{20,}", src),
                          "源码里不能出现 API Key")
        self.assertNotIn("Bearer sk", src)


# ---------------------------------------------------------------------------
#  the real HTTP path, against a loopback stand-in for the providers
#
#  No API key and no internet: a local server speaks the OpenAI-compatible
#  shapes, so urllib/HTTP-error mapping is exercised for real.
# ---------------------------------------------------------------------------
import http.server                                                # noqa: E402
import threading                                                 # noqa: E402
from http.server import BaseHTTPRequestHandler                   # noqa: E402

TEXT_ONLY_BODY = ('{"error":{"code":"1210","message":"messages.content.type '
                  '参数非法，取值范围 [\u0027text\u0027]"}}')


class _Provider(BaseHTTPRequestHandler):
    mode = "ok"                 # class attr, set per test
    seen: list = []

    def log_message(self, *args):        # keep the test output clean
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        type(self).seen.append(("GET", self.path, dict(self.headers)))
        if self.mode == "models-error":
            return self._send(401, '{"error":"invalid key"}')
        self._send(200, '{"object":"list","data":[{"id":"glm-4.6v"},'
                        '{"id":"glm-4-flash"}]}')

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        type(self).seen.append(("POST", self.path, json.loads(body), dict(self.headers)))
        mode = self.mode
        if mode == "text-only":
            return self._send(400, TEXT_ONLY_BODY)
        if mode == "not-found":
            return self._send(404, '{"error":"model not found"}')
        if mode == "quota":
            return self._send(429, '{"error":"insufficient quota"}')
        if mode == "no-json":
            return self._send(200, json.dumps({"choices": [
                {"message": {"content": "I refuse to answer."}}]}))
        self._send(200, json.dumps({"choices": [{"message": {"content": json.dumps(
            {"mode": "reference", "prompt": "a neon cat", "count": 2,
             "width": 1024, "height": 768, "reference_indices": [0],
             "reasoning": "用户要参考这张图"})}}]}))


class TestHttpRoundTrip(unittest.TestCase):
    """Loopback only -- 127.0.0.1, no internet, no key."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.fake = agent.Provider(key="glm", name="fake GLM", base=cls.base,
                                  signup="https://example.invalid",
                                  bare_base64=True)
        cls._patcher = mock.patch.dict(agent.PROVIDERS, {"glm": cls.fake})
        cls._patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Provider.mode = "ok"
        _Provider.seen = []
        self.png = agent._TINY_PNG

    def test_list_models_reaches_the_endpoint_with_the_bearer_token(self):
        models = agent.list_models("glm", "sk-unit-test")
        self.assertEqual(models, ["glm-4-flash", "glm-4.6v"])
        kind, path, headers = _Provider.seen[-1]
        self.assertEqual((kind, path), ("GET", "/models"))
        self.assertEqual(headers.get("Authorization"), "Bearer sk-unit-test")

    def test_list_models_maps_a_bad_key_to_an_agent_error(self):
        _Provider.mode = "models-error"
        with self.assertRaises(agent.AgentError) as ctx:
            agent.list_models("glm", "sk-wrong")
        self.assertIn("401", str(ctx.exception))

    def test_probe_vision_over_http(self):
        tier, _why = agent.probe_vision("glm", "sk-unit-test", "glm-4.6v")
        self.assertEqual(tier, "vision")
        payload = _Provider.seen[-1][2]
        self.assertEqual(payload["max_tokens"], 1)

    def test_text_only_verdict_comes_from_the_provider_itself(self):
        _Provider.mode = "text-only"
        tier, why = agent.probe_vision("glm", "sk-unit-test", "glm-4-flash")
        self.assertEqual(tier, "text")
        self.assertIn("只接受 text", why)

    def test_404_and_429_are_unknown_never_text(self):
        for mode in ("not-found", "quota"):
            _Provider.mode = mode
            self.assertEqual(agent.probe_vision("glm", "sk-unit-test", "x")[0],
                             "unknown", mode)

    def test_ask_full_round_trip_into_a_validated_plan(self):
        data = agent.ask("glm", "sk-unit-test", "glm-4.6v",
                         "参考这张图，画霓虹猫，2 张", [("a.png", self.png)], 1)
        plan = agent.Plan.from_agent(data, 1, MAX_PIXELS)
        self.assertEqual(plan.mode, "reference")
        self.assertEqual(plan.prompt, "a neon cat")
        self.assertEqual(plan.count, 2)
        self.assertEqual(plan.reference_indices, [0])
        self.assertEqual(plan.warnings, [])

    def test_ask_rejects_unparseable_content(self):
        _Provider.mode = "no-json"
        with self.assertRaises(agent.AgentError):
            agent.ask("glm", "sk-unit-test", "glm-4.6v", "x", [], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
