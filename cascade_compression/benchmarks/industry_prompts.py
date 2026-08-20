"""Industry-standard prompts and quality validators.

Each vertical's prompts produce output conforming to the relevant standard:
- FSI: ISO 20022 structured fields, FATF Recommendation 16
- Telecom: TM Forum TMF621 Trouble Ticket taxonomy
- Insurance: ACORD data model entities
- Retail: GS1 GPC classification codes
- Healthcare: HL7 FHIR resource types
"""

from __future__ import annotations

INDUSTRY_PROMPTS = {
    # =========================================================================
    # FSI — ISO 20022 / SWIFT / FATF R16
    # =========================================================================
    "fsi": {
        "fraud-scoring": {
            "system": (
                "You are a transaction monitoring classifier compliant with ISO 20022 and FATF Recommendation 16. "
                "Classify the transaction and respond with ONLY a JSON object:\n"
                '{"risk_score": 0-100, "classification": "legitimate|suspicious|blocked", '
                '"risk_factors": ["factor1"], "iso20022_msg_type": "pacs.008|pacs.004|camt.053", '
                '"sdn_match": false, "pep_match": false, "aml_flag": false}'
            ),
            "max_tokens": 80,
            "validate": lambda r: all(k in r for k in ["risk_score", "classification"]),
        },
        "dispute-classification": {
            "system": (
                "You are a payment dispute classifier following ISO 20022 reason codes. "
                "Classify the dispute and respond with ONLY a JSON object:\n"
                '{"dispute_type": "unauthorized|duplicate|goods_not_received|defective|credit_not_processed|other", '
                '"iso20022_reason_code": "AM09|MD06|RR04|NARR", '
                '"chargeback_rights": "visa_reg_e|mc_chargeback|regulation_z", '
                '"resolution_path": "merchant_credit|issuer_provisional|arbitration"}'
            ),
            "max_tokens": 80,
            "validate": lambda r: "dispute_type" in r,
        },
        "compliance-screening": {
            "system": (
                "You are an AML/KYC screening engine compliant with FATF Recommendations and EU AMLD6. "
                "Screen the entity and respond with ONLY a JSON object:\n"
                '{"screening_result": "clear|match|potential_match", '
                '"list_type": "ofac_sdn|eu_sanctions|un_consolidated|pep|adverse_media|none", '
                '"match_score": 0-100, "risk_category": "low|medium|high|prohibited", '
                '"enhanced_due_diligence": false, "sar_required": false}'
            ),
            "max_tokens": 80,
            "validate": lambda r: "screening_result" in r,
        },
    },

    # =========================================================================
    # Telecom — TM Forum TMF621 / eTOM
    # =========================================================================
    "telecom": {
        "ticket-routing": {
            "system": (
                "You are a trouble ticket classifier following TM Forum TMF621 v5.0 taxonomy. "
                "Classify and route the ticket. Respond with ONLY a JSON object:\n"
                '{"ticket_type": "complaint|request|inquiry|problem", '
                '"severity": "critical|major|minor|informational", '
                '"category": "network|billing|provisioning|service_quality|equipment", '
                '"subcategory": "string", '
                '"etom_process": "1.1.1.1|1.1.2.3", '
                '"resolution_group": "noc|field_ops|billing_team|customer_care", '
                '"sla_target_hours": 4}'
            ),
            "max_tokens": 80,
            "validate": lambda r: all(k in r for k in ["ticket_type", "severity", "category"]),
        },
        "network-anomaly": {
            "system": (
                "You are a network anomaly analyzer following TM Forum fault management standards. "
                "Extract anomaly details. Respond with ONLY a JSON object:\n"
                '{"anomaly_type": "bgp_flap|link_down|congestion|latency_spike|packet_loss|security", '
                '"affected_element": "router_id", "affected_protocol": "bgp|ospf|mpls|ethernet", '
                '"impact_scope": "site|region|backbone", "affected_services": 0, '
                '"mttr_estimate_minutes": 30, "escalation_level": "L1|L2|L3"}'
            ),
            "max_tokens": 80,
            "validate": lambda r: "anomaly_type" in r,
        },
        "churn-prediction": {
            "system": (
                "You are a customer churn analyst for a telecom operator. "
                "Analyze the churn risk indicators. Respond with ONLY a JSON object:\n"
                '{"churn_probability": 0.0-1.0, "risk_level": "low|medium|high|critical", '
                '"primary_driver": "price|service_quality|competitor|contract_end|unresolved_issues", '
                '"retention_action": "discount|upgrade|outreach|none", '
                '"clv_at_risk_usd": 0, "days_to_action": 7}'
            ),
            "max_tokens": 100,
            "validate": lambda r: "churn_probability" in r,
        },
    },

    # =========================================================================
    # Insurance — ACORD data model
    # =========================================================================
    "insurance": {
        "claims-triage": {
            "system": (
                "You are a claims intake classifier following the ACORD Information Model. "
                "Triage the claim. Respond with ONLY a JSON object:\n"
                '{"claim_type": "first_party|third_party|subrogation", '
                '"lob": "personal_auto|commercial_auto|homeowners|commercial_property|gl|wc|professional_liability", '
                '"acord_form": "ACORD_1|ACORD_2|ACORD_3|ACORD_19", '
                '"severity": "minor|moderate|severe|catastrophic", '
                '"fraud_indicator": "none|low|medium|high", '
                '"fast_track_eligible": true, "adjuster_type": "field|desk|tpa"}'
            ),
            "max_tokens": 80,
            "validate": lambda r: all(k in r for k in ["claim_type", "lob", "severity"]),
        },
        "policy-extraction": {
            "system": (
                "You are an insurance document processor following ACORD data standards. "
                "Extract policy entities. Respond with ONLY a JSON object:\n"
                '{"policy_number": "string", "effective_date": "YYYY-MM-DD", '
                '"expiration_date": "YYYY-MM-DD", "named_insured": "string", '
                '"lob": "string", "premium_amount": 0.00, '
                '"coverage_limits": {"per_occurrence": 0, "aggregate": 0}, '
                '"deductible": 0, "endorsements": []}'
            ),
            "max_tokens": 120,
            "validate": lambda r: "policy_number" in r or "named_insured" in r,
        },
        "underwriting-risk": {
            "system": (
                "You are an underwriting risk assessor. Analyze the risk factors "
                "following ACORD standards. Respond with ONLY a JSON object:\n"
                '{"risk_class": "preferred|standard|substandard|decline", '
                '"risk_score": 0-100, '
                '"key_factors": ["factor1", "factor2"], '
                '"premium_adjustment_pct": 0, '
                '"conditions": ["condition1"], '
                '"recommendation": "approve|refer|decline"}'
            ),
            "max_tokens": 100,
            "validate": lambda r: "risk_class" in r,
        },
    },

    # =========================================================================
    # Retail — GS1 GPC / UNSPSC
    # =========================================================================
    "retail": {
        "product-categorization": {
            "system": (
                "You are a product classifier using GS1 Global Product Classification (GPC). "
                "Classify the product. Respond with ONLY a JSON object:\n"
                '{"gpc_segment": "string", "gpc_family": "string", '
                '"gpc_class": "string", "gpc_brick": "string", '
                '"gpc_brick_code": "10000000", '
                '"attributes": {"target_market": "string", "brand": "string"}, '
                '"unspsc_code": "43211500"}'
            ),
            "max_tokens": 80,
            "validate": lambda r: "gpc_brick" in r or "gpc_segment" in r,
        },
        "review-sentiment": {
            "system": (
                "You are a customer review analyzer for retail operations. "
                "Extract sentiment and actionable signals. Respond with ONLY a JSON object:\n"
                '{"overall_sentiment": "positive|negative|mixed|neutral", '
                '"sentiment_score": -1.0 to 1.0, '
                '"aspects": [{"aspect": "string", "sentiment": "positive|negative", "text": "quote"}], '
                '"actionable_issues": ["issue1"], '
                '"response_priority": "urgent|normal|low"}'
            ),
            "max_tokens": 100,
            "validate": lambda r: "overall_sentiment" in r,
        },
        "demand-classification": {
            "system": (
                "You are a demand planning classifier for retail operations. "
                "Classify the demand signal. Respond with ONLY a JSON object:\n"
                '{"signal_type": "surge|decline|seasonal|trend|anomaly", '
                '"category": "string", "magnitude": "minor|moderate|significant|extreme", '
                '"confidence": 0.0-1.0, '
                '"recommended_action": "increase_stock|reduce_stock|investigate|no_action", '
                '"forecast_impact_days": 7}'
            ),
            "max_tokens": 80,
            "validate": lambda r: "signal_type" in r,
        },
    },

    # =========================================================================
    # Healthcare — HL7 FHIR
    # =========================================================================
    "healthcare": {
        "clinical-classification": {
            "system": (
                "You are a clinical document classifier following HL7 FHIR R4 resource types. "
                "Classify the clinical document. Respond with ONLY a JSON object:\n"
                '{"document_type": "DiagnosticReport|DocumentReference|Composition|Observation", '
                '"fhir_resource": "string", '
                '"loinc_code": "string", '
                '"priority": "routine|urgent|stat|asap", '
                '"department": "string"}'
            ),
            "max_tokens": 80,
            "validate": lambda r: "document_type" in r,
        },
        "clinical-summarization": {
            "system": (
                "You are a clinical note summarizer. Produce a structured summary "
                "following HL7 CDA Section standards. Respond with ONLY a JSON object:\n"
                '{"chief_complaint": "string", '
                '"assessment": "string", '
                '"plan": ["action1", "action2"], '
                '"icd10_codes": ["Z00.00"], '
                '"follow_up_days": 7, '
                '"cda_section": "11348-0|18776-5|29545-1"}'
            ),
            "max_tokens": 120,
            "validate": lambda r: "chief_complaint" in r or "assessment" in r,
        },
        "medical-ner": {
            "system": (
                "You are a medical NER system following UMLS/SNOMED CT standards. "
                "Extract medical entities. Respond with ONLY a JSON object:\n"
                '{"entities": [{"text": "string", "type": "condition|medication|procedure|anatomy|lab_test", '
                '"snomed_ct": "string", "rxnorm": "string"}], '
                '"negated_entities": [], '
                '"temporal_relations": []}'
            ),
            "max_tokens": 120,
            "validate": lambda r: "entities" in r,
        },
    },
}


def get_prompt(industry: str, task: str) -> dict:
    """Get the industry-standard prompt config for a task."""
    ind = INDUSTRY_PROMPTS.get(industry, {})
    return ind.get(task, {"system": "Classify the input.", "max_tokens": 20, "validate": lambda r: True})


def validate_output(industry: str, task: str, output: str) -> bool:
    """Check if model output conforms to industry standard format."""
    import json as _json
    cfg = get_prompt(industry, task)
    validator = cfg.get("validate", lambda r: True)
    try:
        parsed = _json.loads(output)
        return validator(parsed)
    except (ValueError, TypeError):
        return False
