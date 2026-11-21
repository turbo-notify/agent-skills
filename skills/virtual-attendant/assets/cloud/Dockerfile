# The virtual attendant on a server: only Python, no agent program, nothing to log in to.
# Build from the skill folder:  docker build -f assets/cloud/Dockerfile -t tn-attendant .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TN_ASSIST_BASE=/data
RUN useradd --create-home --uid 10001 tn && mkdir /data && chown tn /data
COPY scripts /app/scripts
USER tn
VOLUME /data
# Variables (see references/harness-api.md): TURBO_NOTIFY_API_KEY, TN_ASSIST_ORG, TN_ASSIST_NUMBER,
# TN_LLM_API_KEY, TN_LLM_MODEL and, for a model other than OpenAI's, TN_LLM_BASE_URL.
ENTRYPOINT ["python", "/app/scripts/tn_assist"]
CMD ["run", "--harness", "api"]
