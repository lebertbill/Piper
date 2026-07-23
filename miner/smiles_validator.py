"""
SMILES Validator - Validates and canonicalizes SMILES strings.
"""

from typing import Dict, List, Any, Optional, Tuple
import logging

_log = logging.getLogger(__name__)

# Try to import RDKit
try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False
    _log.warning("RDKit not available. SMILES validation will be limited.")


def validate_smiles(smiles: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate a SMILES string and return canonical form.
    
    Args:
        smiles: SMILES string to validate
    
    Returns:
        Tuple of (is_valid, canonical_smiles, error_message)
    """
    if not smiles or not isinstance(smiles, str):
        return False, None, "Empty or invalid SMILES string"
    
    # Clean up the SMILES string
    smiles = smiles.strip()
    
    if not RDKIT_AVAILABLE:
        # Basic validation without RDKit
        # Check for obviously invalid characters
        invalid_chars = set(smiles) - set("CNOPSFClBrIHcnops0123456789[]()=#@+-/\\.")
        if invalid_chars:
            return False, None, f"Invalid characters: {invalid_chars}"
        
        # Return as-is if basic checks pass
        return True, smiles, None
    
    try:
        # Parse SMILES with RDKit
        mol = Chem.MolFromSmiles(smiles)
        
        if mol is None:
            return False, None, "Invalid SMILES structure"
        
        # Get canonical SMILES
        canonical_smiles = Chem.MolToSmiles(mol)
        
        # Get molecular formula for verification
        formula = Chem.rdMolDescriptors.CalcMolFormula(mol)
        
        return True, canonical_smiles, None
        
    except Exception as e:
        return False, None, f"RDKit error: {str(e)}"


def validate_molecule_dict(molecule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate a molecule dictionary and add validation info.
    
    Args:
        molecule: Dictionary with 'label', 'name', 'smiles', etc.
    
    Returns:
        Updated dictionary with validation info
    """
    if 'smiles' not in molecule:
        molecule['smiles_valid'] = False
        molecule['smiles_error'] = "No SMILES provided"
        return molecule
    
    is_valid, canonical, error = validate_smiles(molecule['smiles'])
    
    molecule['smiles_valid'] = is_valid
    
    if is_valid and canonical:
        molecule['smiles_canonical'] = canonical
        
        # Add molecular properties if RDKit is available
        if RDKIT_AVAILABLE:
            try:
                mol = Chem.MolFromSmiles(canonical)
                molecule['molecular_formula'] = Chem.rdMolDescriptors.CalcMolFormula(mol)
                molecule['molecular_weight'] = round(Descriptors.MolWt(mol), 2)
            except:
                pass
    else:
        molecule['smiles_error'] = error
    
    return molecule


def validate_extraction_results(results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate all SMILES in extraction results.
    
    Args:
        results: Extraction results dictionary
    
    Returns:
        Updated results with validation info
    """
    if 'entries' not in results:
        return results
    
    total_molecules = 0
    valid_molecules = 0
    invalid_molecules = []
    
    for entry in results['entries']:
        # Validate reactants
        if 'reactants' in entry:
            for reactant in entry['reactants']:
                total_molecules += 1
                reactant = validate_molecule_dict(reactant)
                if reactant.get('smiles_valid'):
                    valid_molecules += 1
                else:
                    invalid_molecules.append({
                        'entry': entry.get('entry'),
                        'type': 'reactant',
                        'label': reactant.get('label'),
                        'smiles': reactant.get('smiles'),
                        'error': reactant.get('smiles_error')
                    })
        
        # Validate products
        if 'products' in entry:
            for product in entry['products']:
                total_molecules += 1
                product = validate_molecule_dict(product)
                if product.get('smiles_valid'):
                    valid_molecules += 1
                else:
                    invalid_molecules.append({
                        'entry': entry.get('entry'),
                        'type': 'product',
                        'label': product.get('label'),
                        'smiles': product.get('smiles'),
                        'error': product.get('smiles_error')
                    })
        
        # Validate intermediates
        if 'intermediates' in entry:
            for intermediate in entry['intermediates']:
                total_molecules += 1
                intermediate = validate_molecule_dict(intermediate)
                if intermediate.get('smiles_valid'):
                    valid_molecules += 1
                else:
                    invalid_molecules.append({
                        'entry': entry.get('entry'),
                        'type': 'intermediate',
                        'label': intermediate.get('label'),
                        'smiles': intermediate.get('smiles'),
                        'error': intermediate.get('smiles_error')
                    })
    
    # Add validation summary
    results['validation_summary'] = {
        'total_molecules': total_molecules,
        'valid_molecules': valid_molecules,
        'invalid_molecules_count': len(invalid_molecules),
        'validation_rate': round(valid_molecules / total_molecules * 100, 1) if total_molecules > 0 else 0,
        'rdkit_available': RDKIT_AVAILABLE
    }
    
    if invalid_molecules:
        results['invalid_smiles'] = invalid_molecules
    
    return results


# Tested. (The below code can be removed safely)
if __name__ == "__main__":
    # Test SMILES validation
    test_smiles = [
        "C1=CC=C2C(=C1)C(=O)N(C2)Bn",  # Valid
        "C(C(=O)Ph)(C(F)(F)F)",  # Valid
        "INVALID",  # Invalid
        "CCO",  # Valid (ethanol)
    ]
    
    print("SMILES Validation Test:")
    print(f"RDKit available: {RDKIT_AVAILABLE}\n")
    
    for smiles in test_smiles:
        is_valid, canonical, error = validate_smiles(smiles)
        print(f"SMILES: {smiles}")
        print(f"  Valid: {is_valid}")
        if canonical:
            print(f"  Canonical: {canonical}")
        if error:
            print(f"  Error: {error}")
        print()
