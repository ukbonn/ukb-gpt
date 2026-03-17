# Dynamic Model Zoo

This folder stores model families, not host-specific worker topology files.

This page is a model-zoo and deployment-reference page. If you are setting up the stack for the first time, start with [docs/README.md](../../docs/README.md).

## Layout

```text
compose/models/
  backend_router.yml
  llm/
    openai--gpt-oss-120b/
      base.yml
      model.toml
    qwen--qwen3-1.7b/
      base.yml
      model.toml
  embedding/
    alibaba-nlp--gte-qwen2-1.5b-instruct/
      base.yml
      model.toml
    qwen--qwen3-embedding-4b/
      base.yml
      model.toml
  stt/
    mistralai--voxtral-mini-4b-realtime-2602/
      base.yml
      model.toml
```

- `base.yml` is the committed static worker scaffold for one model family.
- `model.toml` declares metadata, worker runtime defaults, optional model variables, and GPU architecture presets.
- Deployment topology is provided separately via `MODEL_DEPLOYMENT_CONFIG`, `EMBEDDING_MODEL_DEPLOYMENT_CONFIG`, and `STT_MODEL_DEPLOYMENT_CONFIG`.
- `start.py` renders concrete compose overrides into `compose/generated/model.<role>.yml` at runtime.

## Deployment Configs

Each deployment config is a small TOML file that selects:

- the model family
- the worker GPU groups
- tensor parallelism
- optional expert parallelism
- GPU architecture preset selection via `gpu_architecture` (`auto`, `default`, or an explicit vendor-scoped preset)

Example:

```toml
api_version = "ukbgpt/v1alpha1"
kind = "model_deployment"
role = "llm"
model_family = "model.llm.openai_gpt_oss_120b"
gpu_architecture = "auto"
router = "auto"

[worker_defaults]
tensor_parallel_size = 2
expert_parallel_enabled = true

[[workers]]
gpus = [0, 1]

[[workers]]
gpus = [2, 3]
```

Path handling:

- direct absolute paths are accepted
- repo-relative paths are accepted

Examples are shipped under [`examples/model_deployments/`](../../examples/model_deployments).
Wizard-created deployment configs are stored under
`compose/generated/deployments/<role>/<family-slug>/deployment-XX.toml`.
That path lives under `compose/generated/`, which is ignored by Git.

`gpu_architecture` means the normalized GPU vendor/architecture class used to select model-specific tuning.
Current built-in values are:

- `auto`: detect from the selected GPUs
- `default`: use only the family's generic settings
- `nvidia_ampere`, `nvidia_hopper`, `nvidia_blackwell`: apply NVIDIA-specific tuning when that family defines it

The `nvidia_` prefix is intentional so the preset space can grow later without ambiguity.

## Environment Variables

Primary backend:

```bash
export MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/qwen3-1.7b.single-gpu.toml"
```

Optional embedding backend:

```bash
export EMBEDDING_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/gte-qwen2.single-gpu.toml"
export EMBEDDING_MODEL_ID="Alibaba-NLP/gte-Qwen2-1.5B-instruct"
```

Alternative embedding backend:

```bash
export EMBEDDING_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/qwen3-embedding-4b.single-gpu.toml"
export EMBEDDING_MODEL_ID="Qwen/Qwen3-Embedding-4B"
```

Optional STT backend:

```bash
export STT_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-mini-4b.single-gpu.toml"
export STT_MODEL_ID="mistralai/Voxtral-Mini-4B-Realtime-2602"
```

Embedding-only deployments are supported:

```bash
export MODEL_DEPLOYMENT_CONFIG=""
export EMBEDDING_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/gte-qwen2.single-gpu.toml"
```

## Developer Notes

If you want to add or extend supported model families, start with:

- [docs/dev/model-families.md](../../docs/dev/model-families.md)
- [docs/dev/extending-the-stack.md](../../docs/dev/extending-the-stack.md)

## Worker Image Resolution

- `VLLM_OPENAI_IMAGE_LLM`
- `VLLM_OPENAI_IMAGE_EMBEDDING`
- `VLLM_OPENAI_IMAGE_STT`
- `VLLM_OPENAI_IMAGE` as the shared fallback

Worker-image defaults are declared per model family in `compose/models/**/model.toml`.
Resolution order is role-specific override, then `VLLM_OPENAI_IMAGE`, then the selected family default.
This lets you point multiple backend classes at the same newer compatible image when you want to avoid pulling different tags.

## `gpt-oss` Encodings

`gpt-oss` families require:

```bash
export GPT_OSS_ENCODINGS_PATH="/path/to/pre-downloaded/harmony-encodings"
```

The worker template mounts this directory read-only at `/etc/encodings`.

<!-- GENERATED_MODELS_START -->
## Model Catalog

This section is generated from `compose/models/**/model.toml` metadata.

### LLM Families

#### OpenAI gpt-oss-120b

High-capacity gpt-oss family with runtime-selected deployment topology and optional architecture tuning.

- Model family ID: `model.llm.openai_gpt_oss_120b`
- Base template: `compose/models/llm/openai--gpt-oss-120b/base.yml`
- Accelerator: `nvidia`
- GPU architecture presets: `default, nvidia_ampere, nvidia_blackwell, nvidia_hopper`
- Default worker image: `vllm/vllm-openai:v0.14.0`

Required model variables:

- `GPT_OSS_ENCODINGS_PATH` (example: `/path/to/pre-downloaded/harmony/encodings`): Host directory containing Harmony encodings required by gpt-oss deployments.

Optional model variables:

- `VLLM_OPENAI_IMAGE_LLM` (example: `vllm/vllm-openai:v0.14.0`): Worker image override for this LLM family.
- `VLLM_LLM_MAX_MODEL_LEN` (example: `131072`): Optional max model length override for this model family.
- `VLLM_LLM_GPU_MEMORY_UTILIZATION` (example: `0.95`): Optional GPU memory utilization fraction override for this model family.

#### Qwen Qwen3-1.7B

General-purpose compact Qwen chat model family.

- Model family ID: `model.llm.qwen_qwen3_1_7b`
- Base template: `compose/models/llm/qwen--qwen3-1.7b/base.yml`
- Accelerator: `nvidia`
- GPU architecture presets: `default`
- Default worker image: `vllm/vllm-openai:v0.14.0`

Optional model variables:

- `VLLM_OPENAI_IMAGE_LLM` (example: `vllm/vllm-openai:v0.14.0`): Worker image override for this LLM family.
- `VLLM_LLM_MAX_MODEL_LEN` (example: `32768`): Optional max model length override for this model family.
- `VLLM_LLM_GPU_MEMORY_UTILIZATION` (example: `0.90`): Optional GPU memory utilization fraction override for this model family.

#### Qwen Qwen3.5-122B-A10B-FP8

Large Qwen3.5 MoE FP8 family for standard multimodal serving with reasoning parser and MTP enabled.

- Model family ID: `model.llm.qwen_qwen3_5_122b_a10b_fp8`
- Base template: `compose/models/llm/qwen--qwen3.5-122b-a10b-fp8/base.yml`
- Accelerator: `nvidia`
- GPU architecture presets: `default, nvidia_ampere`
- Default worker image: `vllm/vllm-openai:nightly`

Optional model variables:

- `VLLM_OPENAI_IMAGE_LLM` (example: `vllm/vllm-openai:nightly`): Worker image override for this LLM family. Use a Qwen3.5-compatible vLLM image.
- `VLLM_LLM_MAX_MODEL_LEN` (example: `262144`): Optional max model length override for this model family.
- `VLLM_LLM_GPU_MEMORY_UTILIZATION` (example: `0.90`): Optional GPU memory utilization fraction override for this model family.

#### Qwen Qwen3.5-0.8B

Small-footprint Qwen model family with optional context and memory tuning.

- Model family ID: `model.llm.qwen_qwen3_5_0_8b`
- Base template: `compose/models/llm/qwen--qwen3.5-0.8b/base.yml`
- Accelerator: `nvidia`
- GPU architecture presets: `default`
- Default worker image: `vllm/vllm-openai:v0.17.1-cu130`

Optional model variables:

- `VLLM_OPENAI_IMAGE_LLM` (example: `vllm/vllm-openai:v0.17.1-cu130`): Worker image override for this LLM family.
- `VLLM_LLM_MAX_MODEL_LEN` (example: `262144`): Optional max model length override for this model family.
- `VLLM_LLM_GPU_MEMORY_UTILIZATION` (example: `0.30`): Optional GPU memory utilization fraction override for this model family.

### Embedding Families

#### Alibaba-NLP gte-Qwen2-1.5B-instruct

Dedicated embedding backend family.

- Model family ID: `model.embedding.alibaba_nlp_gte_qwen2_1_5b`
- Base template: `compose/models/embedding/alibaba-nlp--gte-qwen2-1.5b-instruct/base.yml`
- Accelerator: `nvidia`
- GPU architecture presets: `default`
- Default worker image: `vllm/vllm-openai:v0.14.0`

Optional model variables:

- `VLLM_OPENAI_IMAGE_EMBEDDING` (example: `vllm/vllm-openai:v0.14.0`): Worker image override for this embedding family.
- `VLLM_EMBEDDING_MAX_MODEL_LEN` (example: `32000`): Optional max model length override for embedding workers.
- `VLLM_EMBEDDING_GPU_MEMORY_UTILIZATION` (example: `0.90`): Optional GPU memory utilization fraction override for embedding workers.

#### Qwen Qwen3-Embedding-4B

Instruction-aware Qwen embedding backend with 32k context support.

- Model family ID: `model.embedding.qwen_qwen3_embedding_4b`
- Base template: `compose/models/embedding/qwen--qwen3-embedding-4b/base.yml`
- Accelerator: `nvidia`
- GPU architecture presets: `default`
- Default worker image: `vllm/vllm-openai:v0.14.0`

Optional model variables:

- `VLLM_OPENAI_IMAGE_EMBEDDING` (example: `vllm/vllm-openai:v0.14.0`): Worker image override for this embedding family.
- `VLLM_EMBEDDING_MAX_MODEL_LEN` (example: `32768`): Optional max model length override for embedding workers.
- `VLLM_EMBEDDING_GPU_MEMORY_UTILIZATION` (example: `0.90`): Optional GPU memory utilization fraction override for embedding workers.

### STT Families

#### Mistral Voxtral-Mini-4B-Realtime-2602

Dedicated STT backend family for dictation and transcription workflows.

- Model family ID: `model.stt.mistralai_voxtral_mini_4b_realtime_2602`
- Base template: `compose/models/stt/mistralai--voxtral-mini-4b-realtime-2602/base.yml`
- Accelerator: `nvidia`
- GPU architecture presets: `default`
- Default worker image: `vllm/vllm-openai:nightly`

Optional model variables:

- `VLLM_OPENAI_IMAGE_STT` (example: `vllm/vllm-openai:nightly`): Worker image override for this STT family.
- `VLLM_STT_EXTRA_PIP_PACKAGES` (example: `soxr librosa soundfile mistral_common>=1.9.0`): Optional additional pip packages for STT worker image builds.
- `VLLM_STT_LD_PRELOAD` (example: `/usr/lib/x86_64-linux-gnu/libjemalloc.so.2`): Optional LD_PRELOAD override for STT workers.
- `VLLM_STT_GPU_MEMORY_UTILIZATION` (example: `0.70`): Optional GPU memory utilization fraction override for STT workers.
- `VLLM_STT_MAX_MODEL_LEN` (example: `131072`): Optional max model length override for STT workers.
<!-- GENERATED_MODELS_END -->
