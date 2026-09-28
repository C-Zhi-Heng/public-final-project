# Dataset Attribution — IU X-Ray (OpenI) for MedGemma RadGraph Evaluation

Used for the MedGemma-vs-reference-report RadGraph evaluation described in `plan.md`. Non-commercial academic use only; images/reports are **not** redistributed as part of this project's submitted repository — only this citation and the evaluation methodology/results are.

## Source & Credit

- **Dataset:** "Chest X-rays (Indiana University)"
- **Kaggle uploader:** [raddar](https://www.kaggle.com/raddar)
- **Dataset link:** https://www.kaggle.com/datasets/raddar/chest-xrays-indiana-university
- **License:** [Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0)](https://creativecommons.org/licenses/by-nc-nd/4.0/)

## Authenticity note (uploader's own stated origin)

The Kaggle dataset page states its origin explicitly:

> "Original source: https://openi.nlm.nih.gov/ ... Metadata downloaded using available API (https://openi.nlm.nih.gov/services#searchAPIUsingGET)"

i.e. the uploader (`raddar`) directly attributes this dataset to the National Library of Medicine's Open-i (OpenI) service, with metadata pulled via OpenI's own public API — this is the basis for treating it as a legitimate, traceable derivative of the official OpenI/Indiana University chest X-ray collection rather than an unrelated or unverified copy.

## Academic citation (original OpenI collection)

Per OpenI's attribution request, the original researchers are credited via their published paper:

> Demner-Fushman D, Kohli MD, Rosenman MB, Shooshan SE, Rodriguez L, Antani S, Thoma GR, McDonald CJ. "Preparing a collection of radiology examinations for distribution and retrieval." *J Am Med Inform Assoc.* 2016 Mar;23(2):304-10.
> PubMed: https://pubmed.ncbi.nlm.nih.gov/26133894/

## Usage in this project

- Used only to obtain X-ray image + paired reference report (findings/impression) data for computing `F1RadGraph` scores against MedGemma's generated descriptions.
- No images or report text from this dataset are committed to this repository.
- License terms (non-commercial, no-derivatives) are respected: results/scores are reported in aggregate for academic evaluation, not republished as a derivative dataset.

# Dataset Attribution — ACI-Bench for faster-whisper / phi3:mini Evaluation

Used for the ASR and SOAP-note-quality evaluation described in `plan.md` ("phi3:mini / faster-whisper evaluation"). Academic use only; the dataset is **not** redistributed as part of this project's submitted repository — only this citation, the sampling manifest logic, and the evaluation methodology/results are. The local copy lives outside the repo (`G:\UOL\CM3070-Final-Project\dataset\huggingface-ACI-bench\`).

## Source & Credit

- **Dataset:** ACI-Bench (Ambient Clinical Intelligence Benchmark) — doctor-patient encounter dialogues paired with clinician-written reference visit notes
- **Copy used in this project:** Hugging Face parquet version `mkieffer/ACI-Bench-MedARC`, loaded via the `datasets` library (`virtassist` and `virtscribe` subsets, `test1` split, `transcript_version == "humantrans"` only)
- **Dataset link (used):** https://huggingface.co/datasets/mkieffer/ACI-Bench-MedARC
- **Original data (figshare CSV corpus):** https://figshare.com/articles/dataset/aci-bench-corpus_zip/22494601
- **Original code / evaluation scripts:** https://github.com/wyim/aci-bench
- **License:** [Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)
- **Licence restriction to respect:** the dataset is explicitly not licensed for training models to make medical diagnoses. This project only *evaluates* existing models and does not fine-tune on it, so it is compliant.

## Authenticity note

The Hugging Face copy used here (`mkieffer/ACI-Bench-MedARC`) is a third-party conversion of the original ACI-Bench corpus published by the paper's authors, not the authors' own release. The authoritative sources are the *Scientific Data* paper below, the authors' GitHub repository, and the original figshare corpus, all linked above. The Hugging Face version was chosen for practical reasons (loadable via `datasets`, and an explicit `transcript_version` column that makes the human-transcript-only filter straightforward), and the `download_aci_bench_hf.py` script asserts the expected column names on load so that any structural deviation from the documented schema fails loudly rather than silently. **To do before submission:** confirm the Hugging Face dataset card's own stated origin/licence wording and quote it here, in the same way the IU X-Ray note quotes the Kaggle uploader.

## Academic citation

Per the dataset's CC BY 4.0 licence, the original researchers are credited via their published paper:

> Yim W, Fu Y, Ben Abacha A, Snider N, Lin T, Yetisgen M. "Aci-bench: a Novel Ambient Clinical Intelligence Dataset for Benchmarking Automatic Visit Note Generation." *Scientific Data* 10, 586 (2023).
> Article: https://www.nature.com/articles/s41597-023-02487-3
> PMC: https://pmc.ncbi.nlm.nih.gov/articles/PMC10482860/
> arXiv preprint: https://arxiv.org/abs/2306.02022

## Usage in this project

- **Purpose:** ACI-Bench supplies a ground-truth dialogue script and a clinician-written reference note for each encounter, which lets the pipeline be evaluated end to end (audio → transcript → SOAP note) against known-correct text.
- **Transcription evaluation (faster-whisper):** 12 cases are randomly sampled (`random.Random(42)`, for reproducibility) from the `virtassist` + `virtscribe` `test1` splits, restricted to `humantrans` versions so that the `dialogue` field is a clean human-transcribed reference. Each dialogue is read aloud and recorded with the app's `Recorder`, transcribed by faster-whisper, and scored against the ground-truth dialogue using word error rate (WER) via the Hugging Face `evaluate` library.
- **Downstream SOAP-note evaluation (phi3:mini):** two notes are generated per case, one from the ground-truth dialogue and one from the faster-whisper transcript, and each is scored against the reference note with ROUGE-L and BERTScore F1. The delta between the two scores measures how much ASR error degrades note quality, separately from phi3:mini's own error.
- **Sample-size caveat:** n = 12 is small, so results are reported as indicative rather than statistically robust, consistent with the RadGraph evaluation.
- **Recording caveat:** the dialogues are read aloud by a single speaker rather than spoken by two people in a real consultation, so WER results will not fully reflect real multi-speaker, ambient-room conditions. This should be stated as a limitation in Chapter 5.
- No dialogue or note text from this dataset is committed to this repository. Results and scores are reported in aggregate for academic evaluation, and the models are not fine-tuned on the data.

## Supplementary dataset (not currently used)

- **MTS-Dialog** — Ben Abacha A, Yim W, Fan Y, Lin T. "An Empirical Study of Clinical Note Generation from Doctor-Patient Encounters." *EACL 2023*. https://aclanthology.org/2023.eacl-main.168/ — Data: https://github.com/abachaa/MTS-Dialog — Licence: CC BY 4.0. Kept as a possible secondary sample pool only; not part of the current evaluation.
