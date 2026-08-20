"""Synthetic healthcare signal generator for cascade benchmarking.

Generates HL7 FHIR-style clinical events:
- Normal: routine vitals, scheduled meds, standard labs, regular admissions
- Critical: abnormal labs, medication errors, sepsis indicators, falls
- Compliance: HIPAA access violations, consent gaps, documentation deficiencies
- Operational: bed capacity, staffing alerts, equipment failures
"""

import random
from dataclasses import dataclass, field, asdict
from typing import List


@dataclass
class SyntheticHealthcareEvent:
    id: int
    patient_id: str
    event_type: str
    department: str
    value: str
    unit: str
    reference_range: str
    provider: str
    severity_flag: str
    label: str = "normal"
    label_detail: str = ""


DEPARTMENTS = ["ED", "ICU", "Med-Surg", "Cardiology", "Oncology", "Pediatrics", "OB-GYN", "Orthopedics"]
PROVIDERS = [f"Dr. {name}" for name in ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]]
PATIENTS = [f"MRN-{i:06d}" for i in range(2000)]

NORMAL_VITALS = [
    ("heart_rate", "72", "bpm", "60-100"),
    ("blood_pressure", "120/80", "mmHg", "90/60-140/90"),
    ("temperature", "98.6", "F", "97.0-99.0"),
    ("respiratory_rate", "16", "breaths/min", "12-20"),
    ("oxygen_saturation", "98", "%", "95-100"),
    ("blood_glucose", "95", "mg/dL", "70-140"),
]

NORMAL_LABS = [
    ("cbc_wbc", "7.5", "K/uL", "4.5-11.0"),
    ("cbc_hemoglobin", "14.2", "g/dL", "12.0-17.5"),
    ("bmp_sodium", "140", "mEq/L", "136-145"),
    ("bmp_potassium", "4.2", "mEq/L", "3.5-5.0"),
    ("bmp_creatinine", "1.0", "mg/dL", "0.7-1.3"),
    ("lipid_cholesterol", "195", "mg/dL", "<200"),
]


def generate(count: int = 100_000, seed: int = 42) -> List[SyntheticHealthcareEvent]:
    rng = random.Random(seed)
    events = []
    eid = 0

    # Real-world ratios: AHRQ PSNet (80-99% alarm noise), Joint Commission (~1% critical)
    normal_count = int(count * 0.9500)
    critical_count = max(1, int(count * 0.0100))
    compliance_count = max(1, int(count * 0.0050))
    operational_count = max(1, count - normal_count - critical_count - compliance_count)

    for _ in range(normal_count):
        eid += 1
        etype = rng.choice(["vital_sign", "vital_sign", "vital_sign", "lab_result", "lab_result",
                            "medication_admin", "admission", "discharge", "order_placed", "note_documented"])
        patient = rng.choice(PATIENTS)
        dept = rng.choice(DEPARTMENTS)
        provider = rng.choice(PROVIDERS)

        if etype == "vital_sign":
            name, val, unit, ref = rng.choice(NORMAL_VITALS)
            val = str(round(float(val.split("/")[0]) + rng.uniform(-5, 5), 1))
        elif etype == "lab_result":
            name, val, unit, ref = rng.choice(NORMAL_LABS)
            val = str(round(float(val) + rng.uniform(-1, 1), 1))
        elif etype == "medication_admin":
            name, val, unit, ref = rng.choice([
                ("acetaminophen", "650", "mg", "scheduled"),
                ("metformin", "500", "mg", "scheduled"),
                ("lisinopril", "10", "mg", "scheduled"),
                ("omeprazole", "20", "mg", "scheduled"),
            ])
        else:
            name, val, unit, ref = etype, "", "", ""

        events.append(SyntheticHealthcareEvent(
            id=eid, patient_id=patient, event_type=etype,
            department=dept, value=f"{name}={val}", unit=unit,
            reference_range=ref, provider=provider,
            severity_flag="normal", label="normal",
        ))

    for _ in range(critical_count):
        eid += 1
        crit_type = rng.choice(["abnormal_lab", "critical_vital", "medication_error",
                                "sepsis_indicator", "fall_detected", "cardiac_arrest"])
        patient = rng.choice(PATIENTS)
        dept = rng.choice(["ED", "ICU", "Med-Surg"])
        provider = rng.choice(PROVIDERS)

        if crit_type == "abnormal_lab":
            name, val, unit, ref = rng.choice([
                ("troponin", "2.5", "ng/mL", "<0.04"),
                ("lactate", "6.8", "mmol/L", "0.5-2.0"),
                ("bmp_potassium", "6.9", "mEq/L", "3.5-5.0"),
                ("cbc_wbc", "22.0", "K/uL", "4.5-11.0"),
                ("inr", "5.2", "", "0.8-1.2"),
            ])
            detail = "abnormal_lab_critical"
        elif crit_type == "critical_vital":
            name, val, unit, ref = rng.choice([
                ("blood_pressure", "220/130", "mmHg", "90/60-140/90"),
                ("heart_rate", "180", "bpm", "60-100"),
                ("oxygen_saturation", "82", "%", "95-100"),
                ("temperature", "104.2", "F", "97.0-99.0"),
            ])
            detail = "critical_vital"
        elif crit_type == "medication_error":
            name = rng.choice(["wrong_dose", "wrong_patient", "wrong_drug", "duplicate_admin", "allergy_override"])
            val, unit, ref = name, "", ""
            detail = f"medication_error_{name}"
        elif crit_type == "sepsis_indicator":
            name, val, unit, ref = "sepsis_screen", "positive", "", "negative"
            detail = "sepsis_positive"
        elif crit_type == "fall_detected":
            name, val, unit, ref = "fall_event", "witnessed" if rng.random() > 0.3 else "unwitnessed", "", ""
            detail = "patient_fall"
        else:
            name, val, unit, ref = "code_blue", "activated", "", ""
            detail = "cardiac_arrest"

        events.append(SyntheticHealthcareEvent(
            id=eid, patient_id=patient, event_type=crit_type,
            department=dept, value=f"{name}={val}", unit=unit,
            reference_range=ref, provider=provider,
            severity_flag="critical", label="critical",
            label_detail=detail,
        ))

    for _ in range(compliance_count):
        eid += 1
        comp_type = rng.choice(["hipaa_access", "consent_gap", "documentation_deficiency", "mandatory_reporting"])
        patient = rng.choice(PATIENTS)

        if comp_type == "hipaa_access":
            name = rng.choice(["unauthorized_chart_access", "bulk_record_export", "vip_patient_access", "cross_dept_access"])
            val, unit, ref = name, "", ""
            detail = f"hipaa_{name}"
        elif comp_type == "consent_gap":
            name = rng.choice(["missing_surgical_consent", "expired_consent", "missing_blood_consent"])
            val, unit, ref = name, "", ""
            detail = f"consent_{name}"
        elif comp_type == "documentation_deficiency":
            name = rng.choice(["missing_discharge_summary", "late_operative_note", "incomplete_h_and_p"])
            val, unit, ref = name, "", ""
            detail = f"doc_{name}"
        else:
            name = rng.choice(["suspected_abuse", "communicable_disease", "dog_bite"])
            val, unit, ref = name, "", ""
            detail = f"mandatory_{name}"

        events.append(SyntheticHealthcareEvent(
            id=eid, patient_id=patient, event_type=comp_type,
            department=rng.choice(DEPARTMENTS), value=f"{name}",
            unit="", reference_range="", provider=rng.choice(PROVIDERS),
            severity_flag="compliance", label="compliance",
            label_detail=detail,
        ))

    for _ in range(operational_count):
        eid += 1
        op_type = rng.choice(["bed_capacity", "staffing_alert", "equipment_failure", "supply_shortage"])
        dept = rng.choice(DEPARTMENTS)

        if op_type == "bed_capacity":
            occupancy = rng.randint(85, 105)
            name, val = "bed_occupancy", f"{occupancy}%"
            detail = "bed_over_capacity" if occupancy > 95 else "bed_high_occupancy"
        elif op_type == "staffing_alert":
            name = rng.choice(["nurse_ratio_exceeded", "no_attending_coverage", "tech_shortage"])
            val = name
            detail = f"staffing_{name}"
        elif op_type == "equipment_failure":
            name = rng.choice(["ventilator_malfunction", "monitor_offline", "infusion_pump_error", "ct_scanner_down"])
            val = name
            detail = f"equipment_{name}"
        else:
            name = rng.choice(["ppe_shortage", "blood_supply_low", "medication_backorder"])
            val = name
            detail = f"supply_{name}"

        events.append(SyntheticHealthcareEvent(
            id=eid, patient_id="FACILITY", event_type=op_type,
            department=dept, value=f"{name}={val}", unit="",
            reference_range="", provider="System",
            severity_flag="operational", label="operational",
            label_detail=detail,
        ))

    rng.shuffle(events)
    for i, e in enumerate(events):
        e.id = i + 1
    return events


def label_summary(events):
    counts = {}
    details = {}
    for e in events:
        counts[e.label] = counts.get(e.label, 0) + 1
        if e.label_detail:
            details[e.label_detail] = details.get(e.label_detail, 0) + 1
    return {"total": len(events), "by_label": counts, "by_detail": details}


if __name__ == "__main__":
    evts = generate(100_000)
    s = label_summary(evts)
    print(f"Generated {s['total']} healthcare events:")
    for label, count in sorted(s["by_label"].items()):
        print(f"  {label}: {count}")
