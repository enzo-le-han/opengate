/* --------------------------------------------------
   Copyright (C): OpenGATE Collaboration
   This software is distributed under the terms
   of the GNU Lesser General  Public Licence (LGPL)
   See LICENSE.md for further details
   -------------------------------------------------- */

#ifndef GateSPSEneDistribution_h
#define GateSPSEneDistribution_h

#include "G4SPSEneDistribution.hh"

class GateSPSEneDistribution : public G4SPSEneDistribution {

public:
  virtual ~GateSPSEneDistribution() = default;

  void GenerateFromCDF();

  void GenerateFluor18();

  void GenerateOxygen15();

  void GenerateCarbon11();

  void GenerateRange();

  void GenerateSpectrumLines();

  void GenerateSpectrumHistogram();

  void GenerateSpectrumHistogramInterpolated();

  // Cannot inherit from GenerateOne
  virtual G4double VGenerateOne(G4ParticleDefinition *);

  double fParticleEnergy;

  std::vector<double> fProbabilityCDF;
  std::vector<double> fEnergyCDF;

private:
  std::size_t IndexForProbability(double p) const;
};

#endif // GateSPSEneDistribution_h
