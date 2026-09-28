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