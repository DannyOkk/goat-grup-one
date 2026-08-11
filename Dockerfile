FROM golang:1.26-bookworm AS base
WORKDIR /go/app/base

RUN apt-get update && \
    apt-get install -y build-essential && \
    apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false && \
    rm -rf /var/lib/apt/lists/*

COPY go.mod .
#COPY go.sum .
RUN go mod download
COPY . .

FROM golang:1.26-bookworm AS builder
WORKDIR /go/app/builder

COPY --from=base /go/app/base /go/app/builder

RUN CGO_ENABLED=0 go build -o main -ldflags "-s -w"

FROM gcr.io/distroless/static-debian12 AS production
WORKDIR /go/app/bin

COPY --from=builder /go/app/builder/main /go/app/bin/main

EXPOSE 3000

CMD ["/go/app/bin/main"]