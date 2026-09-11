# Onboarding Guide

Welcome to the project.

This document aims to help new team members set up the necessary access, understand the solution architecture, and get to know the existing environments.

---

# Index

1. #gitlab-access
2. #stackit-access
3. #aws-deloitte-pt-access
4. #repository-management
5. #cluster-setup-no-k9s
6. #migration-architecture
7. #clusters-and-services

---

# GitLab Access

## Objective

GitLab is the central platform used for:

- Source code management
- CI/CD Pipelines
- Helm Charts
- GitOps
- Permission management

## How to request access

1. Open a ticket in `<insert tool>`
2. Request access to the group/project:
   - `<group>`
   - `<subgroup>`
3. Obtain approval from the application owner

## Required information

- Name
- Corporate email
- Project
- Access justification

---

# StackIT Access

## Objective

StackIT is used for accessing and managing the Kubernetes clusters.

## Prerequisites

Before requesting access to StackIT you must have:

- GitLab access
- Active corporate account
- VPN configured (when applicable)

## How to request access

1. Open a ticket in `<tool>`
2. Request access to StackIT
3. Indicate:
   - Project
   - Desired environment
   - Justification

## Validation

After approval validate access using the commands:

```bash
kubectl config get-contexts
kubectl cluster-info
