# Separate SDK runtime. Do not upgrade the production V1 image dependencies.
ARG V2_BASE_IMAGE
FROM ${V2_BASE_IMAGE}
RUN pip install --no-cache-dir 'google-genai==2.29.0'
