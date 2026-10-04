# Third-party data

This repository does **not** redistribute any third-party database. Experiments in the evaluation use the **financial**
database from the [BIRD benchmark](https://bird-bench.github.io/) (BIg Bench for laRge-scale Database grounded text-to-SQL
evaluation), released under
[Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/).
`scripts/download_data.sh` downloads it from the BIRD project and verifies its SHA-256; it is git-ignored.

Everything under `eval/data/` (a small synthetic shop database, its descriptions, and LLM-drafted descriptions of the financial
schema) and every question set under `eval/cases/` and `eval/independent/` was written for this project.

If you use BIRD, please cite:

> Li, J., Hui, B., Qu, G., et al. "Can LLM Already Serve as A Database Interface? A BIg Bench for Large-Scale Database
> Grounded Text-to-SQLs." NeurIPS 2023 Datasets and Benchmarks.
