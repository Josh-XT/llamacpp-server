import os
import logging
from dotenv import load_dotenv
from ezlocalai.LLM import LLM
from ezlocalai.STT import STT
from ezlocalai.CTTS import CTTS
from pyngrok import ngrok

try:
    from ezlocalai.IMG import IMG, img_prompt, create_img_prompt

    img_import_success = True
except ImportError:
    img_import_success = False
    img_prompt = ""
    create_img_prompt = ""


class Pipes:
    def __init__(self):
        load_dotenv()
        logging.info(f"[CTTS] xttsv2_2.0.2 model loading. Please wait...")
        self.ctts = CTTS()
        logging.info(f"[CTTS] xttsv2_2.0.2 model loaded successfully.")
        WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
        self.current_stt = WHISPER_MODEL if WHISPER_MODEL else "base"
        logging.info(f"[STT] {self.current_stt} model loading. Please wait...")
        self.stt = STT(model=self.current_stt)
        logging.info(f"[STT] {self.current_stt} model loaded successfully.")
        DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "phi-2-dpo")
        self.current_llm = DEFAULT_MODEL if DEFAULT_MODEL else "phi-2-dpo"
        logging.info(f"[LLM] {self.current_llm} model loading. Please wait...")
        self.llm = LLM(model=self.current_llm)
        logging.info(f"[LLM] {self.current_llm} model loaded successfully.")
        self.current_vlm = os.getenv("VISION_MODEL", "")
        self.vlm = None
        if self.current_vlm != "":
            self.vlm = LLM(model=self.current_vlm)  # bakllava-1-7b
            logging.info(f"[ezlocalai] Vision is enabled.")
        self.img_enabled = os.getenv("IMG_ENABLED", "false").lower() == "true"
        self.img = None
        if self.img_enabled and img_import_success:
            logging.info(f"[IMG] Image generation is enabled.")
            SD_MODEL = os.getenv("SD_MODEL", "stabilityai/sdxl-turbo")
            logging.info(f"[IMG] sdxl-turbo model loading. Please wait...")
            self.img = IMG(model=SD_MODEL)
            logging.info(f"[IMG] sdxl-turbo model loaded successfully.")
        NGROK_TOKEN = os.environ.get("NGROK_TOKEN", "")
        if NGROK_TOKEN:
            ngrok.set_auth_token(NGROK_TOKEN)
            public_url = ngrok.connect(8091)
            logging.info(f"[ngrok] Public Tunnel: {public_url.public_url}")
            self.local_uri = public_url.public_url
        else:
            self.local_uri = os.environ.get("EZLOCALAI_URL", "http://localhost:8091")

    async def get_response(self, data, completion_type="chat"):
        data["local_uri"] = self.local_uri
        if data["model"]:
            if self.current_llm != data["model"]:
                data["model"] = self.current_llm
        if "stop" in data:
            new_stop = self.llm.params["stop"]
            new_stop.append(data["stop"])
            data["stop"] = new_stop
        if "audio_format" in data:
            base64_audio = (
                data["messages"][-1]["content"]
                if completion_type == "chat"
                else data["prompt"]
            )
            prompt = await self.stt.transcribe_audio(
                base64_audio=base64_audio,
                audio_format=data["audio_format"],
            )
            if completion_type == "chat":
                data["messages"][-1]["content"] = prompt
            else:
                data["prompt"] = prompt
        generated_image = None
        if self.img:
            create_img = self.llm.completion(
                prompt=create_img_prompt.format(
                    prompt=(
                        data["messages"][-1]["content"]
                        if completion_type == "chat"
                        else data["prompt"]
                    )
                ),
                max_tokens=10,
                system_message="Respond with a concise yes or no answer on if it would make sense to generate an image based on the users message. No other explanation is needed!",
            )
            create_img = str(create_img["choices"][0]["text"])
            if create_img.lower().startswith("y"):
                prompt = (
                    data["messages"][-1]["content"]
                    if completion_type == "chat"
                    else data["prompt"]
                )
                image_generation_prompt = self.llm.completion(
                    prompt=img_prompt.format(prompt=prompt),
                    max_tokens=128,
                    system_message="You will now act as a prompt generator for a generative AI called STABLE DIFFUSION. STABLE DIFFUSION generates images based on given prompts.",
                )
                image_generation_prompt = str(
                    image_generation_prompt["choices"][0]["text"]
                )
                if "```python" in image_generation_prompt:
                    image_generation_prompt = image_generation_prompt.split(
                        "```python"
                    )[1]
                    image_generation_prompt = image_generation_prompt.split("```")[0]
                generated_image = self.img.generate(
                    prompt=image_generation_prompt, local_uri=self.local_uri
                )
                if generated_image:
                    prompt += f"\n\nAdditionally, you have used your image creation tool successfully to generate an image with the following stable diffusion description: {image_generation_prompt}.\n\nMention the image you created in the response."
                    if completion_type == "chat":
                        data["messages"][-1]["content"] = prompt
                    else:
                        data["prompt"] = prompt
        if completion_type == "chat":
            response = self.llm.chat(**data)
        else:
            response = self.llm.completion(**data)
        audio_response = None
        if "voice" in data:
            if completion_type == "chat":
                text_response = response["messages"][1]["content"]
            else:
                text_response = response["choices"][0]["text"]
            language = data["language"] if "language" in data else "en"
            if "url_output" in data:
                url_output = data["url_output"].lower() == "true"
            else:
                url_output = True
            audio_response = await self.ctts.generate(
                text=text_response,
                voice=data["voice"],
                language=language,
                local_uri=self.local_uri,
            )
            audio_control = (
                audio_response
                if url_output
                else f"""<audio controls><source src="data:audio/wav;base64,{audio_response}" type="audio/wav"></audio>"""
            )
            if completion_type == "chat":
                response["messages"][1]["content"] = f"{text_response}\n{audio_control}"
            else:
                response["choices"][0]["text"] = f"{text_response}\n{audio_control}"
        if generated_image:
            if completion_type == "chat":
                response["messages"][1]["content"] += f"\n\n{generated_image}"
            else:
                response["choices"][0]["text"] += f"\n\n{generated_image}"
        return response, audio_response
