"""Classification tests driven by real captured subprocess output.

Every fixture below is a verbatim capture from the `my-assistant` sandbox with
the `spokane-evac` policy applied (docs/SOURCES.md). No network, no sandbox, no
subprocess is involved in running them.

The negative controls matter more than the positive ones: an upstream 500
misread as a denial, or a denial misread as "no data", both end with the agent
telling a resident there is no hazard.
"""

from __future__ import annotations

import pytest

from app.egress import Outcome, classify

# Verbatim stderr from `nemoclaw my-assistant exec -- curl https://cameras.alertwildfire.org/x`
# with the host absent from the policy.
TUNNEL_DENIED_STDERR = """\x1b[1m\x1b[32m✓\x1b[39m\x1b[0m Active gateway set to 'nemoclaw'
curl: (56) CONNECT tunnel failed, response 403
nemoclaw: recent network policy denial detected for cameras.alertwildfire.org:443 inside sandbox 'my-assistant'.
  The sandbox's egress policy blocked this request; the tool above only saw the proxy's 403.
  See the denied flow:    nemoclaw my-assistant logs --tail 50
"""

# Verbatim body when the host is allowed but the path is not.
L7_DENIAL_BODY = (
    '{"binary":"/usr/bin/curl","detail":"GET /T4QMspbfLg3qTGWY/arcgis/rest/services '
    'not permitted by policy","error":"policy_denied","host":"services3.arcgis.com",'
    '"layer":"l7","method":"GET","next_steps":[],'
    '"path":"/T4QMspbfLg3qTGWY/arcgis/rest/services","policy":"spokane_evac","port":443,'
    '"protocol":"rest","rule":"GET /T4QMspbfLg3qTGWY/arcgis/rest/services",'
    '"rule_missing":{"binary":"/usr/bin/curl","host":"services3.arcgis.com","layer":"l7",'
    '"method":"GET","path":"/T4QMspbfLg3qTGWY/arcgis/rest/services","port":443,'
    '"type":"rest_allow"}}'
)

GATEWAY_NOISE = "\x1b[1m\x1b[32m✓\x1b[39m\x1b[0m Active gateway set to 'nemoclaw'\n"


def _classify(**kw):
    base = dict(
        url="https://example.test/x",
        host="example.test",
        path="/x",
        method="GET",
        returncode=0,
        body="",
        status=200,
        stderr="",
    )
    base.update(kw)
    return classify(**base)


class TestPolicyDenied:
    def test_connect_tunnel_refusal_is_a_denial_not_an_upstream_error(self):
        r = _classify(
            host="cameras.alertwildfire.org",
            path="/api/nearest",
            returncode=56,
            status=None,
            stderr=TUNNEL_DENIED_STDERR,
        )
        assert r.outcome is Outcome.POLICY_DENIED
        assert r.denial is not None
        assert r.denial.layer == "connect"
        assert r.denial.host == "cameras.alertwildfire.org"

    def test_l7_json_denial_carries_the_policy_and_the_missing_rule(self):
        r = _classify(
            host="services3.arcgis.com",
            path="/T4QMspbfLg3qTGWY/arcgis/rest/services",
            returncode=0,
            status=403,
            body=L7_DENIAL_BODY,
        )
        assert r.outcome is Outcome.POLICY_DENIED
        assert r.denial.policy == "spokane_evac"
        assert r.denial.rule == "GET /T4QMspbfLg3qTGWY/arcgis/rest/services"
        assert "not permitted by policy" in r.denial.detail

    def test_denial_summary_is_renderable_without_further_parsing(self):
        r = _classify(returncode=0, status=403, body=L7_DENIAL_BODY)
        assert "services3.arcgis.com" in r.denial.summary
        assert "spokane_evac" in r.denial.summary


class TestNegativeControls:
    """Each of these, misclassified, would end in a false all-clear."""

    def test_upstream_500_is_not_a_policy_denial(self):
        r = _classify(status=500, body="Internal Server Error")
        assert r.outcome is Outcome.UPSTREAM_ERROR
        assert r.denial is None

    def test_upstream_403_without_the_marker_is_not_a_policy_denial(self):
        # An upstream that rate-limits us with its own 403 must not be reported
        # as containment working correctly.
        r = _classify(status=403, body='{"error":"Too many requests"}')
        assert r.outcome is Outcome.UPSTREAM_ERROR
        assert r.denial is None

    def test_curl_timeout_is_upstream_not_sandbox(self):
        r = _classify(
            returncode=28,
            status=None,
            stderr="curl: (28) Operation timed out after 30001 milliseconds",
        )
        assert r.outcome is Outcome.UPSTREAM_ERROR

    def test_empty_200_is_ok_but_carries_no_data(self):
        # "Source returned nothing" is a real answer and must stay distinct from
        # "source was blocked" and from "source said no hazard".
        r = _classify(status=200, body="")
        assert r.outcome is Outcome.OK
        assert r.json() is None

    def test_unparseable_body_returns_none_rather_than_raising(self):
        r = _classify(status=200, body="<html>gateway timeout</html>")
        assert r.outcome is Outcome.OK
        assert r.json() is None


class TestSandboxUnavailable:
    def test_missing_nemoclaw_binary_is_sandbox_unavailable(self):
        r = _classify(
            returncode=127,
            status=None,
            stderr="nemoclaw: command not found",
        )
        assert r.outcome is Outcome.SANDBOX_UNAVAILABLE

    def test_sandbox_failure_never_masquerades_as_a_denial(self):
        r = _classify(
            returncode=1,
            status=None,
            stderr="nemoclaw: sandbox 'my-assistant' is not running",
        )
        assert r.outcome is Outcome.SANDBOX_UNAVAILABLE
        assert r.denial is None


class TestOk:
    def test_2xx_is_ok(self):
        r = _classify(status=200, body='{"code":"Ok"}')
        assert r.outcome is Outcome.OK
        assert r.json() == {"code": "Ok"}

    @pytest.mark.parametrize("code", [200, 201, 204, 299])
    def test_all_2xx_are_ok(self, code):
        assert _classify(status=code).outcome is Outcome.OK

    def test_gateway_banner_goes_to_stderr_and_never_pollutes_the_body(self):
        # nemoclaw prints its banner on stderr; if it ever moved to stdout the
        # JSON parse below would fail and every source would look empty.
        r = _classify(status=200, body='{"a":1}', stderr=GATEWAY_NOISE)
        assert r.json() == {"a": 1}


class TestStatusSentinel:
    def test_sentinel_is_stripped_from_the_body(self):
        from app.egress import _split_status

        body, status = _split_status('{"a":1}\n__EVAC_HTTP__:200')
        assert body == '{"a":1}'
        assert status == 200

    def test_body_containing_the_sentinel_text_uses_the_last_occurrence(self):
        from app.egress import _split_status

        body, status = _split_status(
            '{"note":"\\n__EVAC_HTTP__:999"}\n__EVAC_HTTP__:200'
        )
        assert status == 200
        assert body == '{"note":"\\n__EVAC_HTTP__:999"}'

    def test_http_000_becomes_none(self):
        from app.egress import _split_status

        _, status = _split_status("\n__EVAC_HTTP__:000")
        assert status is None


class TestCredentialRedaction:
    def test_sensitive_query_values_are_removed_from_recorded_urls(self):
        from app.egress import _redact_url

        safe = _redact_url(
            "https://api.mapbox.com/search?q=Spokane&access_token=pk.example&limit=1"
        )
        assert "pk.example" not in safe
        assert "access_token=[REDACTED]" in safe
        assert "q=Spokane" in safe

    def test_firms_path_key_is_removed_from_recorded_urls(self):
        from app.egress import _redact_url

        safe = _redact_url(
            "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
            "secret-map-key/VIIRS_NOAA20_NRT/-118,47,-117,48/1"
        )
        assert "secret-map-key" not in safe
        assert "/csv/[REDACTED]/VIIRS_NOAA20_NRT/" in safe


class TestRequestHeaders:
    def test_openaq_placeholder_can_be_sent_as_a_controlled_header(self):
        from app.egress import _curl_args

        placeholder = "openshell:resolve:env:OPENAQ_API_KEY"
        argv = _curl_args(
            "https://api.openaq.org/v3/locations",
            method="GET",
            timeout=10,
            headers={"X-API-Key": placeholder, "Accept": "application/json"},
        )
        assert f"X-API-Key: {placeholder}" in argv
        assert "Accept: application/json" in argv

    def test_routing_and_credential_headers_are_not_arbitrarily_extensible(self):
        from app.egress import _curl_args

        with pytest.raises(ValueError, match="not allowed"):
            _curl_args(
                "https://api.openaq.org/v3/locations",
                method="GET",
                timeout=10,
                headers={"Authorization": "Bearer surprise"},
            )

    def test_header_newlines_are_rejected(self):
        from app.egress import _curl_args

        with pytest.raises(ValueError, match="control lines"):
            _curl_args(
                "https://api.openaq.org/v3/locations",
                method="GET",
                timeout=10,
                headers={"X-API-Key": "placeholder\r\nHost: attacker.invalid"},
            )
