# ── Build stage ────────────────────────────────────────────────────────────────
FROM maven:3.9-eclipse-temurin-21-alpine AS build

WORKDIR /app

# Cache Maven dependencies before copying source
COPY pom.xml .
RUN mvn dependency:go-offline -q

COPY src ./src
RUN mvn package -DskipTests -q

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM eclipse-temurin:21-jre-alpine

WORKDIR /app

# Create non-root user
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

COPY --from=build /app/target/joke-application-0.0.1-SNAPSHOT.jar app.jar
RUN chown appuser:appgroup app.jar

USER appuser

EXPOSE 8080

# Container-aware JVM heap sizing
ENTRYPOINT ["java", \
  "-XX:+UseContainerSupport", \
  "-XX:MaxRAMPercentage=75.0", \
  "-jar", "app.jar"]
