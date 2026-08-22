
This is a **{model_quant}** **MFlux** version of [**{model_src}**]({model_src_url}).

[**MFlux**](https://github.com/filipstrand/mflux) runs the latest state-of-the-art generative image models locally on your Mac in native MLX. MFlux is opensource and free.

### Other MFlux versions of {model_src}:
|   QUANT   | GB  | URL |
| :--: | :-: | --- |
| BF16 | {bf16_gb} | {bf16_url} |
|  Q8  | {q8_gb} | {q8_url} |
|  Q6  | {q6_gb} | {q6_url} |
|  Q5  | {q5_gb} | {q5_url} |
|  Q4  | {q4_gb} | {q4_url} |
|  Q3  | {q3_gb} | {q3_url} |

Converted with MFlux version {conversion_mflux_ver} on {conversion_date}. 

Standard MFlux quantization uses the MLX affine quantization across the text-encoder, transformers and VAE.

### Usage

**Installing MFlux**
```
uv tool install --upgrade mflux
```

**Generate an image using {model_src} {model_quant}:**
```
{mflux_cli} \
  --prompt "A puffin standing on a cliff" \
  --width 1280 \
  --height 500 \
  --seed 42 \
  --steps {model_steps} \
  -q {model_quant_integer}
```


