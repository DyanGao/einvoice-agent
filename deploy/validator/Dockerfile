# Validator sidecar: KoSIT validator with the XRechnung scenario configuration,
# running in daemon (HTTP) mode. Versions are pinned; bump them deliberately.
# 21.0.13_9-jre is what 21-jre pointed to on 2026-11-15 (same digest).
FROM eclipse-temurin:21.0.13_9-jre AS fetch
ARG KOSIT_VALIDATOR_VERSION=1.6.3
ARG XRECHNUNG_CONFIG_RELEASE=2026-08-31
ARG XRECHNUNG_VERSION=3.0.2
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/validator
RUN curl -fsSL -o validator.jar \
      "https://github.com/itplr-kosit/validator/releases/download/v${KOSIT_VALIDATOR_VERSION}/validator-${KOSIT_VALIDATOR_VERSION}-standalone.jar" \
 && curl -fsSL -o config.zip \
      "https://github.com/itplr-kosit/validator-configuration-xrechnung/releases/download/v${XRECHNUNG_CONFIG_RELEASE}/xrechnung-${XRECHNUNG_VERSION}-validator-configuration-${XRECHNUNG_CONFIG_RELEASE}.zip" \
 && unzip -q config.zip -d config && rm config.zip

FROM eclipse-temurin:21.0.13_9-jre
RUN useradd --system --no-create-home validator
COPY --from=fetch /opt/validator /opt/validator
WORKDIR /opt/validator
USER validator
EXPOSE 8080
CMD ["java", "-jar", "validator.jar", "-s", "config/scenarios.xml", "-r", "config", "-D", "-H", "0.0.0.0", "-P", "8080"]
