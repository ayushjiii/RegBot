import os
import json
from dotenv import load_dotenv, find_dotenv
from google import genai
from google.genai import types
from ai_agent_db import ScopedSession, FieldMappingCache

load_dotenv(find_dotenv())

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY is not set. Check your .env file.")

client = genai.Client(api_key=api_key)


def ask_brain_to_map_fields(domain, scraped_fields, profile_keys):
    final_decision_matrix = {}
    unmapped_fields = []

    session = ScopedSession()

    print("Checking local mapping cache...")
    for field in scraped_fields:
        placeholder = field["placeholder"]
        cached_record = session.query(FieldMappingCache).filter_by(
            website_domain=domain,
            html_placeholder=placeholder
        ).first()

        if cached_record and cached_record.mapped_db_key and cached_record.mapped_db_key != "None":
            final_decision_matrix[placeholder] = cached_record.mapped_db_key
        else:
            unmapped_fields.append(field)

    if not unmapped_fields:
        print("Cache hit. Skipping AI inference.")
        ScopedSession.remove()
        return final_decision_matrix

    print(f"Cache miss for {len(unmapped_fields)} fields. Invoking Gemini API...")

    prompt = f"""
        You are an RPA data mapper. Map the WEBPAGE FIELDS to the DATABASE KEYS.

        DATABASE KEYS AVAILABLE:
        {json.dumps(profile_keys, indent=2)}

        WEBPAGE FIELDS TO MAP:
        {json.dumps(unmapped_fields, indent=2)}

        INSTRUCTIONS:
        1. Keys: Left side must be the exact "placeholder" string from WEBPAGE FIELDS.
        2. Values: Right side must be the exact string from DATABASE KEYS.
        3. Genders & Checkboxes: Output "GENERATED_TEXT: True" or "GENERATED_TEXT: False".
        4. Dropdowns: Pick the best option from the 'options' list and output "GENERATED_TEXT: [choice]".
        5. Unmappable fields (dates, etc): Output "GENERATED_TEXT: [realistic value]".
        6. Never map a field to its own placeholder name. Do not use null.
        7. CRITICAL: Output ONLY a single JSON dictionary object. Do NOT wrap the output in a list [].
        """

    try:
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                top_p=0.95
            )
        )

        ai_mapping = json.loads(response.text)

        # BOUNTY FIX: If the AI hallucinates and wraps the dict in a list, unwrap it.
        if isinstance(ai_mapping, list):
            if len(ai_mapping) > 0 and isinstance(ai_mapping[0], dict):
                ai_mapping = ai_mapping[0]
            else:
                raise ValueError("AI returned a malformed list instead of a mapping dictionary.")

        for placeholder, decision in ai_mapping.items():
            if not decision or str(decision).strip().lower() == "null":
                continue

            final_decision_matrix[placeholder] = str(decision)

            existing = session.query(FieldMappingCache).filter_by(
                website_domain=domain,
                html_placeholder=placeholder
            ).first()

            if not existing:
                new_cache_entry = FieldMappingCache(
                    website_domain=domain,
                    html_placeholder=placeholder,
                    mapped_db_key=str(decision)
                )
                session.add(new_cache_entry)

        session.commit()
        print("New field rules cached to database.")

    except json.JSONDecodeError as je:
        print(f"JSON Parsing Error: {je}")
    except Exception as e:
        print(f"AI Mapping API Error: {e}")
    finally:
        ScopedSession.remove()

    return final_decision_matrix