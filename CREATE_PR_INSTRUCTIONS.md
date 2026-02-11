# How to Create the Pull Request to stage5

## Option 1: Using GitHub Web Interface (Recommended)

1. **Go to the GitHub repository**:
   - Navigate to https://github.com/BnS-OnM/BenSo-tec

2. **Create the Pull Request**:
   - Click on "Pull requests" tab
   - Click the green "New pull request" button
   - Set the **base branch** to: `stage5`
   - Set the **compare branch** to: `copilot/fix-product-uom-keyerror`
   - Click "Create pull request"

3. **Fill in the PR details**:
   - **Title**: `Fix RPC Error: Use product_uom_id instead of product_uom in purchase wizard`
   - **Description**: Copy the content from `PR_DESCRIPTION.md` file

4. **Create the PR**:
   - Click "Create pull request"

## Option 2: Using GitHub CLI (if installed)

```bash
cd /home/runner/work/BenSo-tec/BenSo-tec

gh pr create \
  --base stage5 \
  --head copilot/fix-product-uom-keyerror \
  --title "Fix RPC Error: Use product_uom_id instead of product_uom in purchase wizard" \
  --body-file PR_DESCRIPTION.md
```

## Option 3: Direct Link

Click this link to create the PR directly:
https://github.com/BnS-OnM/BenSo-tec/compare/stage5...copilot/fix-product-uom-keyerror

## PR Summary

- **From branch**: `copilot/fix-product-uom-keyerror`
- **To branch**: `stage5`
- **Commits**: 5 commits (cd2c70c, 8680bc7, f7fa09b, 9502e71, 8b2f357)
- **Files changed**: 10 files
- **Changes**: +696 additions, -8 deletions

## What This PR Does

Fixes the critical error:
```
ValueError: Invalid field 'product_uom' on model 'purchase.order.line'
```

By using the correct field name `product_uom_id` (with `_id` suffix) when creating purchase order lines from PDF imports.

## After Creating the PR

1. Assign reviewers if needed
2. Add labels (e.g., "bug fix", "high priority")
3. Link to any related issues
4. Wait for CI/CD checks to pass
5. Request review and merge when approved
