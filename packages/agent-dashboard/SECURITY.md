# Security policy

## Supported versions

Security fixes are provided for the latest release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not include real credentials, customer data, or production logs in a report.

## Demo boundary

This project ships synthetic fixtures and a read-only demo API. It is not an authorization layer, a secrets store, or a production agent control plane. Production adopters must authenticate requests, authorize every action server-side, rate-limit retries, validate upstream payloads, and remove sensitive log content before rendering it.
