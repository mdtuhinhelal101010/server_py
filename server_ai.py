from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pdf2image import convert_from_bytes
from PIL import Image
import io
import json
import os
import re
import pytesseract

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Groq(api_key=os.environ.get("GROQ_API_KEY") or os.environ.get("GEMINI_API_KEY"))

PROMPT_TEMPLATE = """
You are an expert data extraction assistant for Indian Visa applications from Bangladesh.
Analyze the provided document text and extract all relevant details into a strict JSON object with the exact keys specified below. 

STRICT EXTRACTION RULES:
1. PRESENT & PERMANENT ADDRESS MAPPING:
   - `pres_add1` and `perm_address1`: Extract exact primary address line from the PDF (e.g., "156") without any extra text[cite: 2].
   - `pres_add2` and `perm_address2`: Extract exact secondary address line from the PDF (e.g., "BHAGOBOTITOLA, MAHESHPUR, SAMONTA")[cite: 2].
   - `pres_town` and `perm_town`: Extract exact City/Town from the PDF (e.g., "JHENAIDAH")[cite: 2].
2. FAMILY BIRTHPLACES & NATIONALITY:
   - `mother_place_of_birth`: Extract exact place of birth as shown in the PDF (e.g., "JHENAIDAH")[cite: 2].
   - `spouse_place_of_birth`: Extract exact place of birth as shown in the PDF (e.g., "NODIA")[cite: 2].
   - `father_nationality`: Extract exact nationality or default to "BANGLADESHI"[cite: 2].
3. PORT OF ARRIVAL & EXIT:
   - `entrypoint`: Extract exact Port of Arrival from the PDF (e.g., "BY RAIL GEDE/BYROAD HARIDASPUR")[cite: 3].
   - `exitpoint`: Extract exact Port of Exit from the PDF (e.g., "BY RAIL GEDE/BYROAD HARIDASPUR")[cite: 3].
4. REFERENCE DETAILS (INDIA):
   - `add1ofsponsor_ind`: Extract exact address line 1 from the India reference section[cite: 3].
   - `add2ofsponsor_ind`: Extract exact address line 2 from the India reference section[cite: 3].
   - `districtofsponsor_ind`: Extract exact district from the India reference section[cite: 3].
5. REFERENCE DETAILS (BANGLADESH):
   - `add1ofsponsor_msn`: Extract exact address line 1 from the Bangladesh reference section[cite: 3].
   - `add2ofsponsor_msn`: Extract exact address line 2 from the Bangladesh reference section[cite: 3].

Keys to extract:
- surname
- givenName
- gender
- dob
- pobTown
- pobCountry
- citizenId
- religion
- identity_marks
- education
- nationality
- acq_nationality
- passNo
- passPlace
- passDate
- passExpire
- pres_add1
- pres_add2
- pres_add3
- pres_town
- pres_country
- pincode
- pres_phone
- isd_code1
- mobile
- email_id
- perm_address1
- perm_address2
- perm_address3
- perm_town
- fthrname
- father_nationality
- father_place_of_birth
- mother_name
- mother_place_of_birth
- marital_status
- spouse_name
- spouse_place_of_birth
- grandparent_flag2

# Profession Details
- occupation
- empname
- empdesignation
- empaddress
- empphone

# Visa Details (Medical)
- hsptNameMsn
- hsptAddMsn
- docNameMsn
- phMsn
- emailMsn
- illness

# Visa Details (General)
- duration
- jouryney_id
- visa_entry_id
- entrypoint
- exitpoint
- visa_serreq_id_112

# Previous Visit Details
- prv_visit_add1
- visited_city
- old_visa_no
- old_visa_type_id
- oldvisaissueplace
- oldvisaissuedateRaw
- refuse_flag2
- country_visited

# Reference Details (India)
- nameofsponsor_ind
- add1ofsponsor_ind
- add2ofsponsor_ind
- stateofsponsor_ind
- districtofsponsor_ind
- phoneofsponsor_ind

# Reference Details (BANGLADESH)
- nameofsponsor_msn
- add1ofsponsor_msn
- add2ofsponsor_msn
- phoneofsponsor_msn

Return ONLY valid JSON format, with no extra text or markdown formatting blocks, just the raw JSON.
"""

def advanced_clean_text(val):
    if not val:
        return ""
    # মাল্টি-লাইন বা লাইন ব্রেক দূর করে স্পেস দিয়ে এক লাইনে রূপান্তর
    text = str(val).replace('\n', ' ').replace('\r', ' ')
    # অতিরিক্ত বা ডাবল স্পেস ক্লিন করা
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def post_process_data(data):
    if not isinstance(data, dict):
        return data

    # সব টেক্সট ফিল্ড থেকে অতিরিক্ত লাইন ব্রেক ও ময়লা পরিষ্কার করা
    for key, val in data.items():
        if isinstance(val, str):
            data[key] = advanced_clean_text(val)

    if not data.get("father_nationality") or data.get("father_nationality").strip() == "":
        data["father_nationality"] = ""
        
    if not data.get("father_place_of_birth") or data.get("father_place_of_birth").strip() == "":
        data["father_place_of_birth"] = data.get("pobTown", "BANGLADESH") if data.get("pobTown") else ""
        
    if not data.get("mother_place_of_birth") or data.get("mother_place_of_birth").strip() == "":
        data["mother_place_of_birth"] = ""
        
    if data.get("spouse_name") and (not data.get("spouse_place_of_birth") or data.get("spouse_place_of_birth").strip() == ""):
        data["spouse_place_of_birth"] = ""

    address_keys = ["pres_add1", "pres_add2", "pres_add3", "perm_address1", "perm_address2", "perm_address3", "empaddress", "hsptAddMsn", "prv_visit_add1", "add1ofsponsor_ind", "add2ofsponsor_ind", "add1ofsponsor_msn", "add2ofsponsor_msn"]
    for k in address_keys:
        if k in data and data[k]:
            cleaned = re.sub(r'^(present\s*address[\s\d]*|permanent\s*address[\s\d]*|address\s*(line)?\s*\d*|line\s*\d*)\s*[:\-]?\s*', '', data[k], flags=re.IGNORECASE)
            data[k] = cleaned.strip()
            
    # টাউন ও অ্যাড্রেস ওভারল্যাপ রোধ করার লজিক
    pob_town = data.get("pobTown", "")
    if pob_town:
        if data.get("pres_add2") and pob_town.lower() in data.get("pres_add2").lower():
            data["pres_add2"] = data["pres_add2"].replace(pob_town, "").strip(" ,-")
        if data.get("perm_address2") and pob_town.lower() in data.get("perm_address2").lower():
            data["perm_address2"] = data["perm_address2"].replace(pob_town, "").strip(" ,-")

    return data

@app.post("/extract-passport")
async def extract_passport(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents))
        extracted_text = pytesseract.image_to_string(image)
        
        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "user",
                    "content": f"{PROMPT_TEMPLATE}\n\nPassport OCR Text Content:\n{extracted_text}"
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=4000
        )
        
        raw_resp = completion.choices[0].message.content.strip()
        parsed_data = json.loads(raw_resp)
        parsed_data = post_process_data(parsed_data)

        return {"status": "success", "raw_text": "Extracted via Groq AI", "parsed_data": parsed_data}
    except Exception as e:
        print("Error in extract-passport:", str(e))
        return {"status": "error", "message": str(e)}

@app.post("/extract-pdf")
async def extract_pdf(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        images = convert_from_bytes(contents)
        if not images:
            raise ValueError("Could not convert PDF to images.")
        
        extracted_text = ""
        for img in images:
            extracted_text += pytesseract.image_to_string(img) + "\n"

        if not extracted_text.strip():
            extracted_text = "PDF document uploaded for Indian Visa application."

        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "user",
                    "content": f"{PROMPT_TEMPLATE}\n\nPDF Extracted Text Content:\n{extracted_text}"
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=4000
        )
        
        raw_resp = completion.choices[0].message.content.strip()
        parsed_data = json.loads(raw_resp)
        parsed_data = post_process_data(parsed_data)

        return {"status": "success", "raw_text": "Extracted via Groq AI PDF", "parsed_data": parsed_data}
    except Exception as e:
        print("Error in extract-pdf:", str(e))
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
