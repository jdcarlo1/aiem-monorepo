import { type ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { useGetSessionStatus } from "@workspace/api-client-react";
import { useSessionId } from "@/hooks/useSessionId";
import { useEagerRestore } from "@/hooks/useAutoRestore";
import { Button } from "@/components/ui/button";
import {
  Brain,
  ChevronLeft,
  ArrowRight,
  Heart,
  Wind,
  Zap,
  Activity,
  Droplets,
  Pill,
  FlaskConical,
  BookOpen,
  Flame,
  Lock,
  Syringe,
  Bone,
  Stethoscope,
  Bug,
  Baby,
  HeartPulse,
  ShieldAlert,
  Radiation,
  Monitor,
  Waves,
  TestTube,
  ListChecks,
  GripVertical,
  Calculator,
  Bandage,
  ClipboardList,
  ShieldCheck,
  Scissors,
  Droplet,
  Utensils,
  Sparkles,
  Eye,
  AlertTriangle,
  Thermometer,
} from "lucide-react";

const fundamentals = [
  {
    category: "Fundamentals of Nursing",
    label: "Fundamentals of Nursing",
    icon: <BookOpen className="w-6 h-6" />,
    questions: 30,
    desc: "Patient safety, infection control, vital signs, wound care, documentation, communication, and core nursing skills.",
    color: "bg-blue-50 text-blue-700 border-blue-200",
    iconBg: "bg-blue-100",
  },
];

const medsurg = [
  {
    category: "MedSurg: Cardiac",
    label: "Cardiac System",
    icon: <Heart className="w-6 h-6" />,
    questions: 30,
    desc: "Heart failure, MI, dysrhythmias, hypertension, cardiac assessment, and hemodynamic monitoring.",
    color: "bg-red-50 text-red-700 border-red-200",
    iconBg: "bg-red-100",
  },
  {
    category: "MedSurg: Respiratory",
    label: "Respiratory System",
    icon: <Wind className="w-6 h-6" />,
    questions: 30,
    desc: "COPD, pneumonia, asthma, ARDS, oxygen therapy, ventilators, and pulmonary assessment.",
    color: "bg-sky-50 text-sky-700 border-sky-200",
    iconBg: "bg-sky-100",
  },
  {
    category: "MedSurg: Neurological",
    label: "Neurological System",
    icon: <Zap className="w-6 h-6" />,
    questions: 30,
    desc: "Stroke, TBI, seizures, ICP management, neuro assessment, and spinal cord injuries.",
    color: "bg-purple-50 text-purple-700 border-purple-200",
    iconBg: "bg-purple-100",
  },
  {
    category: "MedSurg: Endocrine",
    label: "Endocrine System",
    icon: <Activity className="w-6 h-6" />,
    questions: 30,
    desc: "Diabetes, thyroid disorders, adrenal dysfunction, DKA, HHNS, and hormonal imbalances.",
    color: "bg-orange-50 text-orange-700 border-orange-200",
    iconBg: "bg-orange-100",
  },
  {
    category: "MedSurg: Renal & Urology",
    label: "Renal & Urology",
    icon: <Droplets className="w-6 h-6" />,
    questions: 30,
    desc: "AKI, CKD, dialysis, UTIs, fluid/electrolyte balance, and urinary disorders.",
    color: "bg-cyan-50 text-cyan-700 border-cyan-200",
    iconBg: "bg-cyan-100",
  },
  {
    category: "MedSurg: Gastrointestinal",
    label: "Gastrointestinal System",
    icon: <FlaskConical className="w-6 h-6" />,
    questions: 30,
    desc: "GI bleeds, IBD, liver disease, bowel obstruction, GI assessment, and nutrition support.",
    color: "bg-green-50 text-green-700 border-green-200",
    iconBg: "bg-green-100",
  },
  {
    category: "MedSurg: Burns & Integumentary",
    label: "Burns & Integumentary",
    icon: <Flame className="w-6 h-6" />,
    questions: 30,
    desc: "Burn classification, fluid resuscitation, wound care, pressure injuries, and skin assessment.",
    color: "bg-amber-50 text-amber-700 border-amber-200",
    iconBg: "bg-amber-100",
  },
  {
    category: "MedSurg: Orthopedic",
    label: "Orthopedic Nursing",
    icon: <Bone className="w-6 h-6" />,
    questions: 30,
    desc: "Fractures, compartment syndrome, joint replacement, traction, cast care, pin sites, and neurovascular assessment.",
    color: "bg-stone-50 text-stone-700 border-stone-200",
    iconBg: "bg-stone-100",
  },
  {
    category: "MedSurg: Chest Tubes",
    label: "Chest Tube Management",
    icon: <Stethoscope className="w-6 h-6" />,
    questions: 30,
    desc: "Chest tube insertion, drainage systems (Pleur-evac), air leaks, tidaling, pneumothorax, and removal procedure.",
    color: "bg-blue-50 text-blue-700 border-blue-200",
    iconBg: "bg-blue-100",
  },
];

const infectiousDisease = [
  {
    category: "Infectious Disease: Tuberculosis",
    label: "Tuberculosis (TB)",
    icon: <Bug className="w-6 h-6" />,
    questions: 30,
    desc: "TB transmission, airborne precautions, RIPE therapy, DOT, Mantoux/IGRA testing, latent vs active TB, and drug side effects.",
    color: "bg-yellow-50 text-yellow-700 border-yellow-200",
    iconBg: "bg-yellow-100",
  },
  {
    category: "Infectious Disease: HIV/AIDS",
    label: "HIV/AIDS Nursing",
    icon: <ShieldAlert className="w-6 h-6" />,
    questions: 30,
    desc: "CD4 counts, viral load, ART adherence, opportunistic infections (PCP, CMV, MAC), PEP, PrEP, and patient education.",
    color: "bg-red-50 text-red-700 border-red-200",
    iconBg: "bg-red-100",
  },
];

const specialtyNursing = [
  {
    category: "Pediatric Nursing",
    label: "Pediatric Nursing",
    icon: <Baby className="w-6 h-6" />,
    questions: 30,
    desc: "Developmental milestones, immunizations, pediatric vital signs, weight-based dosing, dehydration, and common pediatric conditions.",
    color: "bg-pink-50 text-pink-700 border-pink-200",
    iconBg: "bg-pink-100",
  },
  {
    category: "Maternity & OB Nursing",
    label: "Maternity & OB Nursing",
    icon: <HeartPulse className="w-6 h-6" />,
    questions: 30,
    desc: "Prenatal care, fetal monitoring, labor stages, preeclampsia, postpartum hemorrhage, APGAR scoring, and newborn assessment.",
    color: "bg-rose-50 text-rose-700 border-rose-200",
    iconBg: "bg-rose-100",
  },
  {
    category: "NICU & Neonatal Care",
    label: "NICU & Neonatal Care",
    icon: <Baby className="w-6 h-6" />,
    questions: 50,
    desc: "Fetal heart rate monitoring (early/late/variable decelerations, Category I-III strips, sinusoidal pattern), APGAR scoring, neonatal resuscitation (NRP), RDS vs. TTN vs. meconium aspiration, hyperbilirubinemia/phototherapy, prematurity complications (NEC, IVH, ROP, BPD), congenital anomalies (TEF, CDH, omphalocele), TORCH infections, GBS prophylaxis, neonatal abstinence syndrome, and newborn medications.",
    color: "bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200",
    iconBg: "bg-fuchsia-100",
    badge: "New",
  },
  {
    category: "Psychiatric/Mental Health",
    label: "Psychiatric / Mental Health",
    icon: <Brain className="w-6 h-6" />,
    questions: 30,
    desc: "Therapeutic communication, schizophrenia, bipolar disorder, depression, anxiety, antipsychotics, lithium toxicity, and crisis intervention.",
    color: "bg-indigo-50 text-indigo-700 border-indigo-200",
    iconBg: "bg-indigo-100",
  },
  {
    category: "Oncology Nursing",
    label: "Oncology Nursing",
    icon: <Radiation className="w-6 h-6" />,
    questions: 30,
    desc: "Chemotherapy side effects, neutropenic precautions, tumor lysis syndrome, SVCS, nadir, vesicants, port-a-cath care, and palliative care.",
    color: "bg-purple-50 text-purple-700 border-purple-200",
    iconBg: "bg-purple-100",
  },
  {
    category: "Seizure & Epilepsy Nursing",
    label: "Seizure & Epilepsy Nursing",
    icon: <Zap className="w-6 h-6" />,
    questions: 30,
    desc: "Seizure types (tonic-clonic, absence, focal, status epilepticus), seizure precautions, AED pharmacology (phenytoin, Keppra, valproic acid), postictal care, febrile seizures, and eclampsia management.",
    color: "bg-amber-50 text-amber-700 border-amber-200",
    iconBg: "bg-amber-100",
  },
  {
    category: "Reproductive System",
    label: "Reproductive System",
    icon: <HeartPulse className="w-6 h-6" />,
    questions: 50,
    desc: "Breast disorders, mastectomy care, lymphedema, prostate cancer, TURP, radical prostatectomy, BPH, testicular torsion, testicular cancer, endometriosis, ovarian cancer, hysterectomy, PID, cervical cancer, and postmenopausal bleeding.",
    color: "bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200",
    iconBg: "bg-fuchsia-100",
  },
];

const advancedPractice = [
  {
    category: "Critical Care/ICU",
    label: "Critical Care / ICU",
    icon: <Monitor className="w-6 h-6" />,
    questions: 30,
    desc: "Mechanical ventilation, VAP prevention, hemodynamic monitoring, sepsis bundle, delirium (CAM-ICU), and ACLS medications.",
    color: "bg-slate-50 text-slate-700 border-slate-200",
    iconBg: "bg-slate-100",
  },
  {
    category: "Fluid & Electrolytes",
    label: "Fluid & Electrolytes",
    icon: <Waves className="w-6 h-6" />,
    questions: 30,
    desc: "Sodium, potassium, calcium, magnesium imbalances, acid-base disorders, IV fluid types, SIADH, DI, and refeeding syndrome.",
    color: "bg-teal-50 text-teal-700 border-teal-200",
    iconBg: "bg-teal-100",
  },
];

const clinicalReasoning = [
  {
    category: "ABG Interpretation",
    label: "ABG Interpretation",
    icon: <TestTube className="w-6 h-6" />,
    questions: 30,
    desc: "Step-by-step ABG analysis: respiratory acidosis, respiratory alkalosis, metabolic acidosis, metabolic alkalosis — uncompensated, partially compensated, and fully compensated.",
    color: "bg-lime-50 text-lime-700 border-lime-200",
    iconBg: "bg-lime-100",
  },
  {
    category: "EKG Interpretation",
    label: "EKG / Rhythm Interpretation",
    icon: <Activity className="w-6 h-6" />,
    questions: 30,
    desc: "Normal sinus rhythm, AV blocks (1°, 2° Mobitz I/II, 3°), A-fib, A-flutter, SVT, VT, VF, PVCs, bundle branch blocks, STEMI patterns, and life-threatening arrhythmia management.",
    color: "bg-emerald-50 text-emerald-700 border-emerald-200",
    iconBg: "bg-emerald-100",
  },
];

const pharmacology = [
  {
    category: "Pharmacology: Antidepressants",
    label: "Antidepressant Drugs",
    icon: <Brain className="w-6 h-6" />,
    questions: 30,
    desc: "SSRIs, SNRIs, TCAs, MAOIs, bupropion, mirtazapine, and lithium — mechanisms, side effects, black box warnings, drug interactions, serotonin syndrome, and MAOI dietary restrictions.",
    color: "bg-violet-50 text-violet-700 border-violet-200",
    iconBg: "bg-violet-100",
  },
  {
    category: "Pharmacology: Cardiac Meds",
    label: "Cardiac Medications",
    icon: <Pill className="w-6 h-6" />,
    questions: 30,
    desc: "Beta blockers, ACE inhibitors, digoxin, antiarrhythmics, diuretics, and antianginals.",
    color: "bg-rose-50 text-rose-700 border-rose-200",
    iconBg: "bg-rose-100",
  },
  {
    category: "Pharmacology: Respiratory Meds",
    label: "Respiratory Medications",
    icon: <Wind className="w-6 h-6" />,
    questions: 30,
    desc: "Bronchodilators, corticosteroids, mucolytics, and oxygen therapy pharmacology.",
    color: "bg-indigo-50 text-indigo-700 border-indigo-200",
    iconBg: "bg-indigo-100",
  },
  {
    category: "Pharmacology: Diabetes & Insulin",
    label: "Diabetes & Insulin",
    icon: <Syringe className="w-6 h-6" />,
    questions: 30,
    desc: "Insulin types, oral hypoglycemics, administration timing, hypoglycemia, and DKA management.",
    color: "bg-teal-50 text-teal-700 border-teal-200",
    iconBg: "bg-teal-100",
  },
  {
    category: "Pharmacology: Anticoagulation",
    label: "Anticoagulation / Coumadin",
    icon: <Droplets className="w-6 h-6" />,
    questions: 30,
    desc: "Warfarin, heparin, DOACs, INR monitoring, reversal agents, and bleeding precautions.",
    color: "bg-red-50 text-red-700 border-red-200",
    iconBg: "bg-red-100",
  },
];

const nursingSkillsLab = [
  {
    category: "Nursing Skills Lab",
    label: "Nursing Skills Lab",
    icon: <ClipboardList className="w-6 h-6" />,
    questions: 50,
    desc: "Sterile technique, Foley catheter insertion (male & female), tracheostomy care and suctioning, IV insertion, NG tube placement, central line management, hand hygiene, ostomy care (colostomy, ileostomy, urostomy) — every procedural skill tested on NCLEX.",
    color: "bg-teal-50 text-teal-700 border-teal-200",
    iconBg: "bg-teal-100",
    badge: "Skills",
  },
];

const woundCare = [
  {
    category: "Wound Care Management",
    label: "Wound Care Management",
    icon: <Bandage className="w-6 h-6" />,
    questions: 30,
    desc: "Pressure injury staging (Stage I–IV, unstageable, DTPI), wound VAC (NPWT) setup and troubleshooting, Braden Scale risk assessment, wound irrigation, débridement types, dressing selection, repositioning schedules, and nutrition for healing.",
    color: "bg-rose-50 text-rose-700 border-rose-200",
    iconBg: "bg-rose-100",
    badge: "Wounds",
  },
];

const dosageCalculations = [
  {
    category: "Dosage Calculations",
    label: "Dosage Calculations",
    icon: <Calculator className="w-6 h-6" />,
    questions: 30,
    desc: "Tablet/liquid dosing, IV rates (mL/hr & gtt/min), weight-based dosing, drip calculations (heparin, dopamine, norepinephrine), unit conversions, reconstitution, safe dose ranges, and pediatric dosing — every calculation type tested on NCLEX.",
    color: "bg-amber-50 text-amber-700 border-amber-200",
    iconBg: "bg-amber-100",
    badge: "Math",
  },
];

const ivTherapy = [
  {
    category: "IV Therapy Skills",
    label: "IV Therapy",
    icon: <Syringe className="w-6 h-6" />,
    questions: 20,
    desc: "Peripheral IV insertion, vein selection, gauge selection for blood transfusion, phlebitis vs. infiltration vs. extravasation, vesicant management, IV fluid types (isotonic/hypotonic/hypertonic), air embolism, IV compatibility, and catheter maintenance.",
    color: "bg-blue-50 text-blue-700 border-blue-200",
    iconBg: "bg-blue-100",
    badge: "Skills",
  },
  {
    category: "Lines & Vascular Access",
    label: "Lines & Vascular Access",
    icon: <Activity className="w-6 h-6" />,
    questions: 50,
    desc: "PICC lines, central venous catheters (CVC), arterial lines (A-line), Swan-Ganz/pulmonary artery catheters, Introducer/Cordis, hemodialysis catheters, intraosseous (IO) access, epidural catheters, JP drains, Hemovac drains, TPN lines, and air embolism prevention.",
    color: "bg-cyan-50 text-cyan-700 border-cyan-200",
    iconBg: "bg-cyan-100",
    badge: "Skills",
  },
  {
    category: "Central Line Management",
    label: "Central Line Management",
    icon: <Monitor className="w-6 h-6" />,
    questions: 50,
    desc: "CLABSI prevention bundle, maximal barrier precautions, chlorhexidine skin prep, CVP monitoring and interpretation, multi-lumen catheter management, TPN through central lines, catheter occlusion and alteplase use, tunneled vs. non-tunneled catheters, implanted ports, and complication recognition.",
    color: "bg-indigo-50 text-indigo-700 border-indigo-200",
    iconBg: "bg-indigo-100",
    badge: "Skills",
  },
];

const hygieneADLs = [
  {
    category: "Hygiene & ADLs",
    label: "Hygiene & ADLs",
    icon: <Sparkles className="w-6 h-6" />,
    questions: 20,
    desc: "Bed bath sequence (clean-to-dirty), oral care for unconscious patients (VAP bundle), denture care, perineal care (front-to-back), occupied bed making, gown changes with IV access, diabetic nail care, eye care, body mechanics, and patient autonomy in hygiene.",
    color: "bg-sky-50 text-sky-700 border-sky-200",
    iconBg: "bg-sky-100",
    badge: "Skills",
  },
];

const safetyMobility = [
  {
    category: "Safety & Mobility",
    label: "Safety & Mobility",
    icon: <ShieldCheck className="w-6 h-6" />,
    questions: 20,
    desc: "Morse Fall Scale interpretation, fall prevention (hourly rounding, bed position, call light), gait belt application, wheelchair transfers (stronger side), mechanical lifts, restraint orders and monitoring (every 2 hours), cane/walker/crutch walking gaits, orthostatic hypotension management.",
    color: "bg-green-50 text-green-700 border-green-200",
    iconBg: "bg-green-100",
    badge: "Skills",
  },
];

const woundDressing = [
  {
    category: "Wound Care & Dressing Changes",
    label: "Wound Care & Dressing Changes",
    icon: <Scissors className="w-6 h-6" />,
    questions: 20,
    desc: "Sterile dressing change sequence, wet-to-dry débridement, JP and Hemovac drain management (recompression technique), suture and staple removal timing, wound dehiscence and evisceration response, dressing selection (alginate/hydrogel/film), wound packing, and documentation.",
    color: "bg-orange-50 text-orange-700 border-orange-200",
    iconBg: "bg-orange-100",
    badge: "Skills",
  },
];

const eliminationSkills = [
  {
    category: "Elimination Skills",
    label: "Elimination Skills",
    icon: <Droplet className="w-6 h-6" />,
    questions: 20,
    desc: "Enema administration (position, insertion depth, hang height), fecal impaction assessment and management, bladder scanner interpretation (PVR), condom catheter application, post-void residual management, ostomy output monitoring, bowel sounds auscultation, bladder training for urge incontinence.",
    color: "bg-cyan-50 text-cyan-700 border-cyan-200",
    iconBg: "bg-cyan-100",
    badge: "Skills",
  },
];

const respiratorySkills = [
  {
    category: "Respiratory Care Skills",
    label: "Respiratory Care Skills",
    icon: <Wind className="w-6 h-6" />,
    questions: 20,
    desc: "Oxygen delivery devices (nasal cannula max 6 L/min, simple mask min 5 L/min, NRB bag rule, Venturi mask for COPD), incentive spirometry technique, MDI with spacer, suction indication vs. routine schedule, closed-system suctioning, pursed-lip breathing, SpO₂ interpretation, and diaphragmatic breathing.",
    color: "bg-indigo-50 text-indigo-700 border-indigo-200",
    iconBg: "bg-indigo-100",
    badge: "Skills",
  },
];

const giNutritionSkills = [
  {
    category: "GI & Nutrition Skills",
    label: "GI & Nutrition Skills",
    icon: <Utensils className="w-6 h-6" />,
    questions: 20,
    desc: "NG tube insertion and X-ray verification (gold standard), HOB elevation ≥30° for tube feedings, gastric residual volume management (return the aspirate), TPN via central access (blood glucose monitoring), I&O calculation, aspiration risk assessment, dysphagia diet modifications (nectar-thick), and PEG tube care.",
    color: "bg-lime-50 text-lime-700 border-lime-200",
    iconBg: "bg-lime-100",
    badge: "Skills",
  },
];

const ngnFormats = [
  {
    category: "Select All That Apply",
    label: "Select All That Apply (SATA)",
    icon: <ListChecks className="w-6 h-6" />,
    questions: 30,
    desc: "Master the most commonly missed NCLEX question type. Select every correct answer — partial credit isn't given on the real exam.",
    color: "bg-violet-50 text-violet-700 border-violet-200",
    iconBg: "bg-violet-100",
  },
  {
    category: "Drag & Drop Ordering",
    label: "Drag & Drop Ordering",
    icon: <GripVertical className="w-6 h-6" />,
    questions: 30,
    desc: "Put nursing interventions, assessment steps, and clinical protocols in the correct priority order — just like the real Next Generation NCLEX.",
    color: "bg-orange-50 text-orange-700 border-orange-200",
    iconBg: "bg-orange-100",
  },
  {
    category: "EKG Strip Recognition",
    label: "EKG Strip Recognition",
    icon: <Activity className="w-6 h-6" />,
    questions: 20,
    desc: "Read real EKG strips and identify the rhythm — NSR, A-fib, V-tach, V-fib, heart blocks, STEMI, SVT, and more. Strips display right in the question, exactly like NGN clinical exhibits.",
    color: "bg-rose-50 text-rose-700 border-rose-200",
    iconBg: "bg-rose-100",
    badge: "Picture Questions",
  },
];

const hematologic = [
  {
    category: "Hematologic Disorders",
    label: "Hematologic Disorders",
    icon: <Droplets className="w-6 h-6" />,
    questions: 30,
    desc: "Anemia, sickle cell disease, hemophilia, DIC, thrombocytopenia, polycythemia, blood transfusion reactions, and CBC interpretation.",
    color: "bg-red-50 text-red-700 border-red-200",
    iconBg: "bg-red-100",
  },
];

const immuneRheum = [
  {
    category: "Immune & Rheumatologic Disorders",
    label: "Immune & Rheumatologic Disorders",
    icon: <ShieldAlert className="w-6 h-6" />,
    questions: 30,
    desc: "SLE, rheumatoid arthritis, gout, ankylosing spondylitis, scleroderma, fibromyalgia, anaphylaxis, and immunosuppressant therapy.",
    color: "bg-orange-50 text-orange-700 border-orange-200",
    iconBg: "bg-orange-100",
  },
];

const sensory = [
  {
    category: "Sensory Disorders",
    label: "Sensory Disorders",
    icon: <Eye className="w-6 h-6" />,
    questions: 30,
    desc: "Glaucoma, cataracts, macular degeneration, retinal detachment, Meniere's disease, otitis media/externa, hearing loss, and sensory aid management.",
    color: "bg-sky-50 text-sky-700 border-sky-200",
    iconBg: "bg-sky-100",
  },
];

const perioperative = [
  {
    category: "Perioperative Care",
    label: "Perioperative Care",
    icon: <Scissors className="w-6 h-6" />,
    questions: 30,
    desc: "Pre-op assessment, NPO guidelines, informed consent, intraoperative nursing, PACU management, malignant hyperthermia, DVT prevention, and post-op complications.",
    color: "bg-slate-50 text-slate-700 border-slate-200",
    iconBg: "bg-slate-100",
  },
];

const painManagement = [
  {
    category: "Pain Management",
    label: "Pain Management",
    icon: <Zap className="w-6 h-6" />,
    questions: 30,
    desc: "Pain assessment scales, opioid pharmacology, multimodal analgesia, PCA, epidural management, non-pharmacologic interventions, and opioid toxicity reversal.",
    color: "bg-yellow-50 text-yellow-700 border-yellow-200",
    iconBg: "bg-yellow-100",
  },
];

const infectionInflammation = [
  {
    category: "Infection & Inflammation",
    label: "Infection & Inflammation",
    icon: <Bug className="w-6 h-6" />,
    questions: 30,
    desc: "SIRS, sepsis criteria, isolation precautions (contact, droplet, airborne), MRSA, C. diff, VAP/CLABSI/CAUTI prevention bundles, and antibiotic stewardship.",
    color: "bg-lime-50 text-lime-700 border-lime-200",
    iconBg: "bg-lime-100",
  },
];

const shockSepsis = [
  {
    category: "Shock, Sepsis & Multi-Organ Dysfunction",
    label: "Shock, Sepsis & Multi-Organ Dysfunction",
    icon: <AlertTriangle className="w-6 h-6" />,
    questions: 30,
    desc: "Hypovolemic, cardiogenic, distributive, and obstructive shock; septic shock vasopressors; Hour-1 Bundle; MODS; lactate monitoring; and hemodynamic resuscitation endpoints.",
    color: "bg-rose-50 text-rose-700 border-rose-200",
    iconBg: "bg-rose-100",
  },
];

const endOfLife = [
  {
    category: "End-of-Life & Palliative Care",
    label: "End-of-Life & Palliative Care",
    icon: <HeartPulse className="w-6 h-6" />,
    questions: 30,
    desc: "Palliative vs. hospice care, advance directives, DNR/POLST, comfort-focused medications, signs of approaching death, grief support, and ethical principles (double effect, autonomy).",
    color: "bg-violet-50 text-violet-700 border-violet-200",
    iconBg: "bg-violet-100",
  },
];

const emergencyCritical = [
  {
    category: "Emergency & Critical Care",
    label: "Emergency & Critical Care",
    icon: <Thermometer className="w-6 h-6" />,
    questions: 30,
    desc: "Triage priorities, stroke protocol, ACLS rhythms, DKA/HHS management, overdose antidotes, trauma assessment (ABCDE), anaphylaxis, post-ROSC care, and hypertensive emergencies.",
    color: "bg-amber-50 text-amber-700 border-amber-200",
    iconBg: "bg-amber-100",
  },
  {
    category: "ACLS: Adult Advanced Cardiac Life Support",
    label: "ACLS: Adult Advanced Cardiac Life Support",
    icon: <HeartPulse className="w-6 h-6" />,
    questions: 50,
    desc: "BLS/CPR quality, shockable vs. non-shockable rhythms, defibrillation and synchronized cardioversion, epinephrine and amiodarone dosing, bradycardia and tachycardia algorithms, reversible causes (H and T), post-ROSC care, targeted temperature management, stroke recognition, and team dynamics.",
    color: "bg-red-50 text-red-700 border-red-200",
    iconBg: "bg-red-100",
    badge: "Advanced",
  },
  {
    category: "PALS: Pediatric Advanced Life Support",
    label: "PALS: Pediatric Advanced Life Support",
    icon: <Heart className="w-6 h-6" />,
    questions: 50,
    desc: "Infant and child BLS differences, pediatric assessment triangle, weight-based defibrillation (2 J/kg), epinephrine dosing, SVT in children, pediatric bradycardia algorithm, septic shock resuscitation, respiratory failure, foreign body airway obstruction, drowning, anaphylaxis, neonatal resuscitation, and PALS chain of survival.",
    color: "bg-pink-50 text-pink-700 border-pink-200",
    iconBg: "bg-pink-100",
    badge: "Advanced",
  },
];

type CategoryItem = {
  category: string;
  label: string;
  icon: ReactNode;
  questions: number;
  desc: string;
  color: string;
  iconBg: string;
  badge?: string;
};

function CategoryCard({
  item,
  isSubscribed,
  onLockClick,
}: {
  item: CategoryItem;
  isSubscribed: boolean;
  onLockClick: () => void;
}) {
  const encodedCategory = encodeURIComponent(item.category);

  if (!isSubscribed) {
    return (
      <button
        onClick={onLockClick}
        className={`w-full text-left p-5 rounded-2xl border-2 bg-card border-border hover:border-primary/30 hover:shadow-sm transition-all duration-200 opacity-80 cursor-pointer`}
      >
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center shrink-0 text-muted-foreground">
            <Lock className="w-5 h-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <h3 className="font-semibold text-foreground">{item.label}</h3>
              <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-muted text-muted-foreground">{item.questions} questions</span>
              {item.badge && (
                <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 border border-rose-200">{item.badge}</span>
              )}
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed">{item.desc}</p>
          </div>
        </div>
      </button>
    );
  }

  return (
    <Link href={`/study?category=${encodedCategory}`}>
      <div className={`w-full text-left p-5 rounded-2xl border-2 bg-card border-border hover:border-primary/40 hover:shadow-md transition-all duration-200 cursor-pointer group`}>
        <div className="flex items-start gap-4">
          <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${item.iconBg}`}>
            <div className={item.color.split(" ")[1]}>{item.icon}</div>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <h3 className="font-semibold text-foreground group-hover:text-primary transition-colors">{item.label}</h3>
              <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-primary/10 text-primary">{item.questions} questions</span>
              {item.badge && (
                <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 border border-rose-200">{item.badge}</span>
              )}
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed">{item.desc}</p>
          </div>
          <ArrowRight className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors shrink-0 mt-1" />
        </div>
      </div>
    </Link>
  );
}

export default function NursingSchool() {
  const [, setLocation] = useLocation();
  const sessionId = useSessionId();
  const { data: sessionStatus } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  useEagerRestore(sessionId, sessionStatus?.isSubscribed);

  const isSubscribed = sessionStatus?.isSubscribed ?? false;

  const handleLockClick = () => setLocation("/paywall");

  const totalCategories = fundamentals.length + medsurg.length + infectiousDisease.length + specialtyNursing.length + advancedPractice.length + clinicalReasoning.length + pharmacology.length + nursingSkillsLab.length + woundCare.length + dosageCalculations.length + ngnFormats.length + ivTherapy.length + hygieneADLs.length + safetyMobility.length + woundDressing.length + eliminationSkills.length + respiratorySkills.length + giNutritionSkills.length + hematologic.length + immuneRheum.length + sensory.length + perioperative.length + painManagement.length + infectionInflammation.length + shockSepsis.length + endOfLife.length + emergencyCritical.length;
  const totalQuestions = totalCategories * 30 - 60;

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <Link href="/" className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
            <ChevronLeft className="w-4 h-4 mr-1" />
            Home
          </Link>
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-primary" />
            <span className="text-sm font-bold text-primary">NCLEX AI</span>
          </div>
          <div className="w-16" />
        </div>
      </header>

      <main className="flex-1 w-full max-w-4xl mx-auto px-4 py-8 sm:px-6 pb-16">
        <div className="mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 text-primary text-xs font-bold mb-4 border border-primary/20">
            <BookOpen className="w-3.5 h-3.5" />
            Nursing School Question Banks
          </div>
          <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight mb-3">
            Study by System. <span className="text-primary">Ace Every Exam.</span>
          </h1>
          <p className="text-muted-foreground text-lg leading-relaxed max-w-2xl">
            {totalCategories} question banks covering every nursing specialty — {totalQuestions}+ targeted practice questions. Pick exactly what you're studying and drill it until it clicks.
          </p>
          {!isSubscribed && (
            <div className="mt-4 flex items-center gap-3 p-4 rounded-xl bg-primary/5 border border-primary/20">
              <Lock className="w-5 h-5 text-primary shrink-0" />
              <p className="text-sm text-foreground">
                <span className="font-semibold">Premium feature.</span> Unlock all {totalCategories} question banks with a $49 lifetime plan.{" "}
                <button onClick={handleLockClick} className="text-primary font-semibold underline underline-offset-2 hover:no-underline">
                  Unlock now →
                </button>
              </p>
            </div>
          )}
        </div>

        {/* Fundamentals */}
        <section className="mb-10">
          <h2 className="text-lg font-bold tracking-tight mb-1">Semester 1 — Fundamentals</h2>
          <p className="text-sm text-muted-foreground mb-4">The foundation every nursing student builds on first.</p>
          <div className="space-y-3">
            {fundamentals.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Med-Surg by System */}
        <section className="mb-10">
          <h2 className="text-lg font-bold tracking-tight mb-1">Medical-Surgical — By Body System</h2>
          <p className="text-sm text-muted-foreground mb-4">Study exactly the system you're covering in class this week — including orthopedics and chest tube management.</p>
          <div className="space-y-3">
            {medsurg.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Infectious Disease */}
        <section className="mb-10">
          <h2 className="text-lg font-bold tracking-tight mb-1">Infectious Disease</h2>
          <p className="text-sm text-muted-foreground mb-4">Transmission, isolation precautions, drug therapy, and patient education for high-stakes infections.</p>
          <div className="space-y-3">
            {infectiousDisease.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Specialty Nursing */}
        <section className="mb-10">
          <h2 className="text-lg font-bold tracking-tight mb-1">Specialty Nursing</h2>
          <p className="text-sm text-muted-foreground mb-4">Pediatrics, maternity, psychiatry, and oncology — the four high-yield specialties on every nursing exam.</p>
          <div className="space-y-3">
            {specialtyNursing.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Advanced Practice */}
        <section className="mb-10">
          <h2 className="text-lg font-bold tracking-tight mb-1">Advanced Practice</h2>
          <p className="text-sm text-muted-foreground mb-4">ICU-level critical thinking and fluid/electrolyte mastery — essential for senior semesters and NCLEX.</p>
          <div className="space-y-3">
            {advancedPractice.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Advanced Clinical Topics */}
        <section className="mb-10">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-100 text-rose-700 text-xs font-bold mb-3 border border-rose-200">
            <AlertTriangle className="w-3 h-3" />
            New Categories
          </div>
          <h2 className="text-lg font-bold tracking-tight mb-1">Advanced Clinical Topics</h2>
          <p className="text-sm text-muted-foreground mb-4">High-acuity clinical areas that appear on NCLEX and in practice — from hematology and sensory disorders to emergency response and end-of-life care.</p>
          <div className="space-y-3">
            {hematologic.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {immuneRheum.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {sensory.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {perioperative.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {painManagement.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {infectionInflammation.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {shockSepsis.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {endOfLife.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {emergencyCritical.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Clinical Reasoning */}
        <section className="mb-10">
          <h2 className="text-lg font-bold tracking-tight mb-1">Clinical Reasoning</h2>
          <p className="text-sm text-muted-foreground mb-4">Master ABG interpretation and EKG rhythms — two of the highest-yield skills on NCLEX and in clinical practice.</p>
          <div className="space-y-3">
            {clinicalReasoning.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Pharmacology */}
        <section className="mb-10">
          <h2 className="text-lg font-bold tracking-tight mb-1">Pharmacology</h2>
          <p className="text-sm text-muted-foreground mb-4">Meds, dosing, interactions, and nursing implications — by drug class.</p>
          <div className="space-y-3">
            {pharmacology.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Nursing Skills Lab */}
        <section className="mb-10">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-teal-100 text-teal-700 text-xs font-bold mb-3 border border-teal-200">
            <ClipboardList className="w-3 h-3" />
            Procedures
          </div>
          <h2 className="text-lg font-bold tracking-tight mb-1">Nursing Skills Lab</h2>
          <p className="text-sm text-muted-foreground mb-4">Every hands-on procedural skill tested on NCLEX — from sterile technique and ostomy care to IV therapy, respiratory management, and patient safety.</p>
          <div className="space-y-3">
            {nursingSkillsLab.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {ivTherapy.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {hygieneADLs.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {safetyMobility.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {woundDressing.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {eliminationSkills.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {respiratorySkills.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
            {giNutritionSkills.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Wound Care Management */}
        <section className="mb-10">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-100 text-rose-700 text-xs font-bold mb-3 border border-rose-200">
            <Bandage className="w-3 h-3" />
            Wound Care
          </div>
          <h2 className="text-lg font-bold tracking-tight mb-1">Wound Care Management</h2>
          <p className="text-sm text-muted-foreground mb-4">Pressure injury staging, wound VAC therapy, Braden Scale, and everything you need for wound-focused NCLEX questions.</p>
          <div className="space-y-3">
            {woundCare.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* Dosage Calculations */}
        <section className="mb-10">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-100 text-amber-700 text-xs font-bold mb-3 border border-amber-200">
            <Calculator className="w-3 h-3" />
            Always on NCLEX
          </div>
          <h2 className="text-lg font-bold tracking-tight mb-1">Dosage Calculations</h2>
          <p className="text-sm text-muted-foreground mb-4">Every calculation type that appears on NCLEX — work through them until the math is automatic.</p>
          <div className="space-y-3">
            {dosageCalculations.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* NGN Question Formats */}
        <section className="mb-10">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-violet-100 text-violet-700 text-xs font-bold mb-3 border border-violet-200">
            <ListChecks className="w-3 h-3" />
            New on NCLEX
          </div>
          <h2 className="text-lg font-bold tracking-tight mb-1">NGN Question Formats</h2>
          <p className="text-sm text-muted-foreground mb-4">The newer NCLEX formats that trip up most test-takers — practice them until they feel natural.</p>
          <div className="space-y-3">
            {ngnFormats.map((item) => (
              <CategoryCard key={item.category} item={item} isSubscribed={isSubscribed} onLockClick={handleLockClick} />
            ))}
          </div>
        </section>

        {/* NCLEX + Interview links */}
        <div className="grid sm:grid-cols-2 gap-4 mt-6">
          <Link href="/quiz">
            <div className="p-5 rounded-2xl border-2 border-border bg-card hover:border-primary/40 hover:shadow-md transition-all duration-200 cursor-pointer group">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center">
                  <Brain className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h3 className="font-semibold group-hover:text-primary transition-colors">NCLEX Prep</h3>
                  <p className="text-xs text-muted-foreground">2,000+ questions, all categories</p>
                </div>
                <ArrowRight className="w-4 h-4 text-muted-foreground group-hover:text-primary ml-auto transition-colors" />
              </div>
            </div>
          </Link>
          <Link href="/interview-prep">
            <div className="p-5 rounded-2xl border-2 border-border bg-card hover:border-primary/40 hover:shadow-md transition-all duration-200 cursor-pointer group">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center">
                  <BookOpen className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h3 className="font-semibold group-hover:text-primary transition-colors">Interview Prep</h3>
                  <p className="text-xs text-muted-foreground">20 real nursing job interview questions</p>
                </div>
                <ArrowRight className="w-4 h-4 text-muted-foreground group-hover:text-primary ml-auto transition-colors" />
              </div>
            </div>
          </Link>
        </div>
      </main>
    </div>
  );
}
